"""Scraper ENISA — Agence européenne pour la cybersécurité.

ENISA (European Union Agency for Cybersecurity) publie ses vacances sur
`www.enisa.europa.eu/careers` (listing HTML statique).

Pattern URL :
    /recruitment/vacancies/{slug}
        ex: /recruitment/vacancies/cybersecurity-officers
        ex: /recruitment/vacancies/threat-and-vulnerability-analyst

⚠️ Localisation :
    ENISA HQ = Athènes (Greece). Bureaux secondaires : Heraklion (Crète)
    et Bruxelles (BE). Sans détection explicite, les offres sont
    classées Country.OTHER. Le scraper parcourt la description du détail
    pour détecter Brussels/Belgium et reclasser en BE.

Description : sélecteur `main article` (~1500-3000 chars de texte propre).
Pas de JSON-LD. Pas de pagination (toutes les vacancies tiennent sur 1 page).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_DEFAULT_COMPANY = "ENISA"
_BASE_HOST = "https://www.enisa.europa.eu"
_DETAIL_SELECTORS: tuple[str, ...] = (
    "main article",
    "main",
    ".content",
    "#content",
)

# Pattern URL : /recruitment/vacancies/{slug}, slug autorisé caractères variés
# (incl. tirets, mais pas de `?` ou `#`).
_JOB_URL_RE = re.compile(
    r"^(?:https://www\.enisa\.europa\.eu)?/recruitment/vacancies/(?P<slug>[a-z0-9][a-z0-9-]+)/?$"
)


def _detect_country_from_text(text: str) -> tuple[Country, str]:
    """Détecte le pays depuis le titre+description.

    ENISA HQ Athènes par défaut (Country.OTHER), reclassement BE si on
    détecte 'Brussels' ou 'Belgium' explicitement.
    """
    if not text:
        return Country.OTHER, "Athens, Greece"
    lc = text.lower()
    if "brussels" in lc or "bruxelles" in lc or "belgium" in lc:
        return Country.BE, "Brussels, Belgium"
    if "heraklion" in lc:
        return Country.OTHER, "Heraklion, Greece"
    return Country.OTHER, "Athens, Greece"


class EnisaScraper(BaseScraper):
    """Scraper ENISA via HTML listing + enrichissement détail."""

    name: ClassVar[str] = "enisa"
    source: ClassVar[JobSource] = JobSource.ENISA

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False

        response = self._http_get(self.config.base_url)
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse ENISA HTML: {e}") from e

        jobs: list[JobBase] = []
        seen_ids: set[str] = set()

        for link in soup.find_all("a", href=True):
            parsed = self._parse_link(link)
            if parsed is None:
                continue
            if parsed.external_id in seen_ids:
                continue
            seen_ids.add(parsed.external_id)
            jobs.append(parsed)

        logger.info("[{}] {} unique vacancy links found", self.name, len(jobs))
        jobs = self._enrich_descriptions_with_country(jobs)
        return jobs, False

    def _parse_link(self, link) -> JobBase | None:  # type: ignore[no-untyped-def]
        href = link.get("href", "")
        match = _JOB_URL_RE.match(href)
        if match is None:
            return None
        slug = match.group("slug")
        # Le link text est typiquement le titre (le listing ENISA fait
        # `<a href="/recruitment/vacancies/{slug}">Title</a>`).
        title = link.get_text(" ", strip=True)
        if not title or len(title) < 4:
            return None

        url = urljoin(_BASE_HOST, href) if not href.startswith("http") else href
        return JobBase(
            source=JobSource.ENISA,
            external_id=slug,
            title=title,
            company=_DEFAULT_COMPANY,
            location="Athens, Greece",  # défaut, ré-évalué via détail
            country=Country.OTHER,
            description=f"{title}. {_DEFAULT_COMPANY} — Athens, Greece.",
            url=url,
            raw_data={"slug": slug, "href": href},
        )

    def _enrich_descriptions_with_country(self, jobs: list[JobBase]) -> list[JobBase]:
        """Fetch chaque page détail, extrait description et reclasse country.

        ENISA peut localiser une offre à Brussels (BE) ou Heraklion. Le
        détail mentionne typiquement 'Place of employment: Athens' ou
        'Brussels'. On scanne titre+description pour reclasser.
        """
        from src.scrapers.base import clean_html_to_text  # noqa: PLC0415

        for job in jobs:
            try:
                response = self._http_get(job.url)
                soup = BeautifulSoup(response.text, "lxml")
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "[{}] detail fetch failed for {}: {}", self.name, job.external_id, e
                )
                continue

            # Description via sélecteurs CSS
            text: str | None = None
            for sel in _DETAIL_SELECTORS:
                elements = soup.select(sel)
                if not elements:
                    continue
                candidate = clean_html_to_text("\n".join(str(e) for e in elements))
                if len(candidate) >= 200:
                    text = candidate[:8000]
                    break
            if text:
                job.description = text

            # Reclassement pays/lieu depuis le texte combiné
            country, location = _detect_country_from_text(
                f"{job.title}\n{job.description}"
            )
            job.country = country
            job.location = location

        return jobs
