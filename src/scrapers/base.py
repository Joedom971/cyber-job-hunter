"""Scraper de base — toutes les protections anti-ban centralisées ici.

Chaque scraper concret hérite de `BaseScraper` et implémente uniquement :

    def fetch_jobs(self, page: int) -> tuple[list[JobBase], bool]:
        # Récupère 1 page d'offres, retourne (jobs, has_next_page).
        # Page indexée à partir de 1.
        ...

Le reste (rate limit, retry, robots.txt, circuit breaker, bot detection,
upsert/mark_inactive, stats du run) est mutualisé via `run()`.
"""

from __future__ import annotations

import random
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import ClassVar
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from src.config import SourceConfig
from src.models import JobBase, JobSource
from src.storage import JobRepository


# ─── Exceptions ──────────────────────────────────────────────────────────


class ScrapeError(Exception):
    """Erreur générique de scraping."""


class BotDetectedError(ScrapeError):
    """Cloudflare / captcha / Just-a-moment détecté → on abort proprement."""


class CircuitOpenError(ScrapeError):
    """Le circuit breaker est ouvert → la source est désactivée temporairement."""


class RateLimitedError(ScrapeError):
    """Réponse 429 reçue."""


class RobotsDisallowedError(ScrapeError):
    """robots.txt interdit l'URL."""


# ─── Résultat d'un run ───────────────────────────────────────────────────


class ScrapeRunResult(BaseModel):
    """Bilan d'une exécution complète d'un scraper."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: JobSource
    started_at: datetime
    finished_at: datetime | None = None
    jobs_fetched: int = 0
    jobs_inserted: int = 0
    jobs_updated: int = 0
    jobs_marked_inactive: int = 0
    pages_visited: int = 0
    errors: list[str] = Field(default_factory=list)
    aborted_reason: str | None = None
    """One of: 'bot_detected' | 'circuit_open' | 'robots_disallow' | None."""

    @property
    def succeeded(self) -> bool:
        return self.aborted_reason is None and not self.errors


# ─── Circuit breaker (in-memory, par scraper) ────────────────────────────


class _CircuitBreaker:
    """Compteur d'erreurs consécutives. Au-delà du seuil → ouvert pour `cooldown` secondes."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 3600.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            self.reset()
            return False
        return True

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._opened_at = time.monotonic()

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None


# ─── BaseScraper ─────────────────────────────────────────────────────────


# Patterns regex de challenge anti-bot (insensibles à la casse).
# Volontairement spécifiques : `cloudflare` brut matchait des URLs de CDN
# publiques (cdnjs.cloudflare.com) → faux positifs. Ici on exige le contexte
# "challenge" (titre, script anti-bot, cookies cf-*).
_BOT_CHALLENGE_PATTERNS = (
    r"<title>\s*just a moment",
    r"<title>\s*attention\s+required",
    r"<title>\s*access\s+denied",
    r"<title>[^<]*captcha",
    r"checking\s+your\s+browser\s+before",
    r"please\s+enable\s+(javascript|cookies)\s+(and\s+(javascript|cookies)\s+)?to\s+continue",
    r"cf-browser-verification",
    r"cf-chl-bypass",
    r"id=\"challenge-form\"",
)


class BaseScraper(ABC):
    """Classe de base pour tous les scrapers du projet.

    Sous-classes : définir `name`, `source` (ClassVar) et implémenter `fetch_jobs`.
    """

    name: ClassVar[str]
    source: ClassVar[JobSource]

    def __init__(
        self,
        config: SourceConfig,
        repo: JobRepository | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.repo = repo
        self._client = client or httpx.Client(
            timeout=config.timeout_seconds,
            headers={"User-Agent": config.user_agent},
            follow_redirects=True,
        )
        self._circuit = _CircuitBreaker(
            failure_threshold=max(3, config.max_retries),
            cooldown_seconds=3600.0,
        )
        self._last_request_monotonic: float = 0.0
        self._robots_cache: dict[str, bool] = {}

    # ─── À implémenter par chaque scraper ────────────────────────────────

    @abstractmethod
    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        """Récupère 1 page de jobs. Retourne (jobs, has_next_page)."""

    # ─── API publique ────────────────────────────────────────────────────

    def run(self) -> ScrapeRunResult:
        """Cycle complet d'un scraping : politesse → fetch → upsert → mark_inactive."""
        result = ScrapeRunResult(
            source=self.source, started_at=datetime.now(timezone.utc)
        )

        if self.config.respect_robots_txt and not self._robots_allowed(self.config.base_url):
            result.aborted_reason = "robots_disallow"
            result.finished_at = datetime.now(timezone.utc)
            logger.warning("[{}] robots.txt disallows {}", self.name, self.config.base_url)
            return result

        all_jobs: list[JobBase] = []
        external_ids: set[str] = set()

        try:
            for page in range(1, self.config.max_pages + 1):
                logger.info("[{}] fetching page {}", self.name, page)
                jobs, has_next = self.fetch_jobs(page)
                jobs = list(jobs)
                all_jobs.extend(jobs)
                external_ids.update(j.external_id for j in jobs)
                result.pages_visited += 1
                if not has_next:
                    break
        except BotDetectedError as e:
            result.aborted_reason = "bot_detected"
            result.errors.append(str(e))
            logger.error("[{}] bot challenge: {}", self.name, e)
        except CircuitOpenError as e:
            result.aborted_reason = "circuit_open"
            result.errors.append(str(e))
            logger.warning("[{}] circuit open, skipping: {}", self.name, e)
        except ScrapeError as e:
            result.errors.append(str(e))
            logger.error("[{}] scrape error: {}", self.name, e)

        result.jobs_fetched = len(all_jobs)

        if self.repo is not None and all_jobs:
            for incoming in all_jobs:
                _, is_new = self.repo.upsert_job(incoming)
                if is_new:
                    result.jobs_inserted += 1
                else:
                    result.jobs_updated += 1

            if not result.aborted_reason:
                # Soft-delete les offres absentes UNIQUEMENT si on a fait un run complet
                # (évite de tout désactiver suite à un bot challenge à mi-parcours)
                result.jobs_marked_inactive = self.repo.mark_inactive(
                    self.source, external_ids
                )

        result.finished_at = datetime.now(timezone.utc)
        logger.success(
            "[{}] done: fetched={}, new={}, updated={}, inactive={}, errors={}",
            self.name, result.jobs_fetched, result.jobs_inserted,
            result.jobs_updated, result.jobs_marked_inactive, len(result.errors),
        )
        return result

    # ─── Helpers HTTP avec protections ───────────────────────────────────

    def _http_get(self, url: str, **kwargs) -> httpx.Response:  # type: ignore[no-untyped-def]
        """GET avec rate limit, jitter, retry exponentiel, circuit breaker, bot detection."""
        if self._circuit.is_open:
            raise CircuitOpenError(f"Circuit open for {self.name}")

        last_exc: Exception | None = None
        attempt = 0
        max_attempts = self.config.max_retries + 1  # 1 essai + N retries

        while attempt < max_attempts:
            self._wait_rate_limit()
            try:
                response = self._client.get(url, **kwargs)
            except httpx.RequestError as e:
                self._circuit.record_failure()
                last_exc = e
                logger.warning(
                    "[{}] HTTP error attempt {}/{}: {}",
                    self.name, attempt + 1, max_attempts, e,
                )
            else:
                if self._is_bot_challenge(response):
                    self._circuit.record_failure()
                    raise BotDetectedError(
                        f"Bot challenge detected at {url} (status={response.status_code})"
                    )

                code = response.status_code
                if 200 <= code < 300:
                    self._circuit.record_success()
                    return response
                if code == 429:
                    self._circuit.record_failure()
                    last_exc = RateLimitedError(f"429 Too Many Requests at {url}")
                elif 500 <= code < 600:
                    self._circuit.record_failure()
                    last_exc = ScrapeError(f"{code} server error at {url}")
                else:
                    # 4xx terminales (404, 403 hors bot, etc.) → pas de retry
                    self._circuit.record_failure()
                    raise ScrapeError(f"HTTP {code} at {url}")

            attempt += 1
            if attempt < max_attempts:
                backoff = self.config.backoff_base_seconds * (3 ** (attempt - 1))
                logger.info(
                    "[{}] backoff {:.1f}s before retry {}/{}",
                    self.name, backoff, attempt + 1, max_attempts,
                )
                time.sleep(backoff)

        raise ScrapeError(
            f"All {max_attempts} attempts failed for {url}: {last_exc}"
        ) from last_exc

    def _wait_rate_limit(self) -> None:
        """Sleep jusqu'à respecter `rate_limit_seconds + random jitter`."""
        wait = self.config.rate_limit_seconds + random.uniform(
            0, self.config.jitter_max_seconds
        )
        elapsed = time.monotonic() - self._last_request_monotonic
        if self._last_request_monotonic > 0 and elapsed < wait:
            time.sleep(wait - elapsed)
        self._last_request_monotonic = time.monotonic()

    def _is_bot_challenge(self, response: httpx.Response) -> bool:
        """True si la réponse a une signature de challenge anti-bot."""
        if response.status_code == 403:
            return True
        if response.headers.get("content-type", "").startswith(
            ("application/json", "application/xml", "text/csv")
        ):
            return False  # JSON/XML/CSV ne contiennent jamais de challenge HTML
        try:
            sample = response.text[:8000]
        except UnicodeDecodeError:
            return False
        return any(
            re.search(p, sample, re.IGNORECASE) for p in _BOT_CHALLENGE_PATTERNS
        )

    def _robots_allowed(self, url: str) -> bool:
        """Check robots.txt. Cache par host. False uniquement si Disallow explicite."""
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        if host in self._robots_cache:
            return self._robots_cache[host]

        allowed = True
        try:
            response = self._client.get(f"{host}/robots.txt")
            if response.status_code == 200:
                rp = RobotFileParser()
                rp.parse(response.text.splitlines())
                allowed = rp.can_fetch(self.config.user_agent, url)
        except (httpx.RequestError, Exception) as e:  # noqa: BLE001
            logger.debug("[{}] robots.txt unreachable: {}", self.name, e)
            allowed = True  # En cas d'erreur, on ne bloque pas (mais on log)

        self._robots_cache[host] = allowed
        return allowed

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BaseScraper:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
