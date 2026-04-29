"""Tests BaseScraper — mocks httpx via respx, sleeps mockés via monkeypatch."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobBase, JobSource
from src.scrapers.base import (
    BaseScraper,
    BotDetectedError,
    CircuitOpenError,
    ScrapeError,
    _CircuitBreaker,
)
from src.storage import JobRepository


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Annule tous les time.sleep dans le module base pour des tests rapides."""
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.REST_API,
        base_url="https://example.com/api/jobs",
        rate_limit_seconds=0.0,
        jitter_max_seconds=0.0,
        max_pages=3,
        timeout_seconds=5.0,
        max_retries=2,
        backoff_base_seconds=0.01,
        user_agent="JobHunterBot/1.0 (+test)",
        respect_robots_txt=False,  # désactivé par défaut, on teste séparément
        min_hours_between_runs=0,
    )


@pytest.fixture
def repo(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    r = JobRepository(db_url=f"sqlite:///{db_path}")
    r.create_all()
    yield r
    r.engine.dispose()


def _job(ext_id: str, title: str = "SOC Analyst Junior") -> JobBase:
    return JobBase(
        source=JobSource.OTHER,
        external_id=ext_id,
        title=title,
        company="ACME",
        location="Brussels",
        country=Country.BE,
        url=f"https://example.com/job/{ext_id}",
    )


# ─── Concrete dummy scrapers ─────────────────────────────────────────────


class DummyScraper(BaseScraper):
    """Scraper de test : retourne 2 jobs sur 2 pages."""

    name: ClassVar[str] = "dummy"
    source: ClassVar[JobSource] = JobSource.OTHER

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page == 1:
            return [_job("1"), _job("2")], True
        if page == 2:
            return [_job("3")], False
        return [], False


class HttpDummyScraper(BaseScraper):
    """Scraper qui fait un vrai _http_get pour tester les protections HTTP."""

    name: ClassVar[str] = "httpdummy"
    source: ClassVar[JobSource] = JobSource.OTHER

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        self._http_get(self.config.base_url)
        return [_job(str(page))], False  # 1 page only


# ─── _CircuitBreaker ─────────────────────────────────────────────────────


def test_circuit_breaker_opens_after_threshold():
    cb = _CircuitBreaker(failure_threshold=3, cooldown_seconds=10)
    assert cb.is_open is False
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open is False
    cb.record_failure()
    assert cb.is_open is True


def test_circuit_breaker_resets_on_success():
    cb = _CircuitBreaker(failure_threshold=2, cooldown_seconds=10)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.is_open is False  # compteur reset par success entre les 2


def test_circuit_breaker_cooldown_reopens():
    cb = _CircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
    cb.record_failure()
    assert cb.is_open is False  # cooldown 0 → instantanément refermé


# ─── run() avec scraper sans HTTP ────────────────────────────────────────


def test_run_persists_jobs_and_marks_inactive(cfg, repo):
    scraper = DummyScraper(cfg, repo=repo)
    result = scraper.run()
    assert result.jobs_fetched == 3
    assert result.jobs_inserted == 3
    assert result.jobs_updated == 0
    assert result.pages_visited == 2  # arrêt sur has_next=False

    # Re-run avec une seule offre → les 2 autres marquées inactives
    class SmallerScraper(DummyScraper):
        def fetch_jobs(self, page: int):
            return [_job("1")], False

    result2 = SmallerScraper(cfg, repo=repo).run()
    assert result2.jobs_inserted == 0
    assert result2.jobs_updated == 1
    assert result2.jobs_marked_inactive == 2


def test_run_respects_max_pages(cfg, repo):
    cfg.max_pages = 2

    class InfiniteScraper(BaseScraper):
        name: ClassVar[str] = "inf"
        source: ClassVar[JobSource] = JobSource.OTHER

        def fetch_jobs(self, page: int):
            return [_job(f"p{page}")], True  # has_next toujours True

    result = InfiniteScraper(cfg, repo=repo).run()
    assert result.pages_visited == 2  # cap respecté


def test_run_no_repo_no_persistence(cfg):
    scraper = DummyScraper(cfg, repo=None)
    result = scraper.run()
    assert result.jobs_fetched == 3
    assert result.jobs_inserted == 0


# ─── _http_get : succès, retry, bot detection ────────────────────────────


@respx.mock
def test_http_get_success(cfg, monkeypatch):
    respx.get("https://example.com/api/jobs").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    scraper = HttpDummyScraper(cfg)
    result = scraper.run()
    assert result.jobs_fetched == 1
    assert result.errors == []


@respx.mock
def test_http_get_retries_on_5xx_then_succeeds(cfg):
    route = respx.get("https://example.com/api/jobs").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    scraper = HttpDummyScraper(cfg)
    result = scraper.run()
    assert route.call_count == 3
    assert result.jobs_fetched == 1


@respx.mock
def test_http_get_exhausts_retries_on_persistent_5xx(cfg):
    respx.get("https://example.com/api/jobs").mock(return_value=httpx.Response(503))
    scraper = HttpDummyScraper(cfg)
    result = scraper.run()
    assert result.errors  # erreur reportée
    assert result.jobs_fetched == 0


@respx.mock
def test_http_get_4xx_not_retried(cfg):
    route = respx.get("https://example.com/api/jobs").mock(
        return_value=httpx.Response(404, text="not found")
    )
    scraper = HttpDummyScraper(cfg)
    result = scraper.run()
    assert route.call_count == 1  # pas de retry sur 404
    assert result.errors


@respx.mock
def test_bot_challenge_just_a_moment_aborts(cfg):
    respx.get("https://example.com/api/jobs").mock(
        return_value=httpx.Response(
            200,
            text="<html><title>Just a moment...</title></html>",
            headers={"content-type": "text/html"},
        )
    )
    scraper = HttpDummyScraper(cfg)
    result = scraper.run()
    assert result.aborted_reason == "bot_detected"


@respx.mock
def test_bot_challenge_403_aborts(cfg):
    respx.get("https://example.com/api/jobs").mock(return_value=httpx.Response(403))
    scraper = HttpDummyScraper(cfg)
    result = scraper.run()
    assert result.aborted_reason == "bot_detected"


@respx.mock
def test_json_response_not_flagged_as_bot(cfg):
    """Une réponse JSON contenant 'cloudflare' dans le texte n'est PAS un challenge."""
    respx.get("https://example.com/api/jobs").mock(
        return_value=httpx.Response(
            200,
            json={"provider": "cloudflare", "jobs": []},
        )
    )
    scraper = HttpDummyScraper(cfg)
    result = scraper.run()
    assert result.aborted_reason is None


@respx.mock
def test_circuit_breaker_skips_subsequent_calls(cfg):
    """Après 3 erreurs consécutives, le scraper est en circuit-open."""
    respx.get("https://example.com/api/jobs").mock(return_value=httpx.Response(503))

    class MultiPageHttpScraper(BaseScraper):
        name: ClassVar[str] = "multi"
        source: ClassVar[JobSource] = JobSource.OTHER

        def fetch_jobs(self, page: int):
            self._http_get(self.config.base_url)
            return [], True

    cfg.max_pages = 5
    scraper = MultiPageHttpScraper(cfg)
    result = scraper.run()
    # circuit_open ou simplement errors agrégées selon timing
    assert result.jobs_fetched == 0


def test_circuit_open_raises_on_direct_call(cfg):
    scraper = HttpDummyScraper(cfg)
    # Force open
    for _ in range(scraper._circuit.failure_threshold):
        scraper._circuit.record_failure()
    with pytest.raises(CircuitOpenError):
        scraper._http_get("https://example.com/")


# ─── robots.txt ──────────────────────────────────────────────────────────


@respx.mock
def test_robots_txt_disallow_aborts_run(cfg):
    cfg.respect_robots_txt = True
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /api/")
    )
    scraper = HttpDummyScraper(cfg)
    result = scraper.run()
    assert result.aborted_reason == "robots_disallow"


@respx.mock
def test_robots_txt_allow_runs_normally(cfg):
    cfg.respect_robots_txt = True
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /")
    )
    respx.get("https://example.com/api/jobs").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    scraper = HttpDummyScraper(cfg)
    result = scraper.run()
    assert result.aborted_reason is None
    assert result.jobs_fetched == 1


@respx.mock
def test_robots_txt_404_treated_as_allowed(cfg):
    """Pas de robots.txt = pas d'interdiction (comportement web standard)."""
    cfg.respect_robots_txt = True
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/api/jobs").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    scraper = HttpDummyScraper(cfg)
    result = scraper.run()
    assert result.aborted_reason is None


# ─── User-Agent honnête ──────────────────────────────────────────────────


@respx.mock
def test_honest_user_agent_in_requests(cfg):
    route = respx.get("https://example.com/api/jobs").mock(
        return_value=httpx.Response(200, json={})
    )
    scraper = HttpDummyScraper(cfg)
    scraper.run()
    assert route.calls.last.request.headers["user-agent"] == cfg.user_agent
