"""Scraper Orange Cyberdefense — TeamTailor public API.

TeamTailor est une plateforme SaaS de recrutement avec API publique :
    GET https://career.teamtailor.com/v1/jobs?company={subdomain}
        OU
    GET https://{subdomain}.teamtailor.com/jobs

Le subdomain Orange Cyberdefense : `orangecyberdefense`. La page publique
sert directement le HTML avec les jobs côté serveur ; on parse en HTML.

Pattern observé : div.box ou article avec a[href*='/jobs/'].
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_DEFAULT_COMPANY = "Orange Cyberdefense"
_DETAIL_SELECTORS: tuple[str, ...] = (
    "main article",
    "article",
    "main",
    "div.entry-content",
)
# Pattern URL TeamTailor : avec ou sans préfixe locale (`/de/jobs/`, `/jobs/`, etc.)
_JOB_URL_RE = re.compile(
    r"^(?:https?://[^/]+)?/(?:[a-z]{2}/)?jobs/(?P<id>\d+)(?:-(?P<slug>[a-z0-9-]+))?$"
)


_BE_CITIES = ("brussels", "bruxelles", "antwerp", "ghent", "namur", "liège", "liege",
              "wavre", "louvain", "mons", "charleroi")


def _country_from_location(loc: str | None) -> Country:
    if not loc:
        return Country.OTHER
    lower = loc.lower()
    if any(c in lower for c in _BE_CITIES) or "belgium" in lower:
        return Country.BE
    if "luxembourg" in lower:
        return Country.LU
    if "france" in lower or "paris" in lower:
        return Country.FR
    return Country.OTHER


class OrangeCyberdefenseScraper(BaseScraper):
    """Scraper Orange Cyberdefense via TeamTailor public HTML."""

    name: ClassVar[str] = "orange_cyberdefense"
    source: ClassVar[JobSource] = JobSource.ORANGE_CYBERDEFENSE

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False

        response = self._http_get(self.config.base_url)
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse Orange Cyberdefense HTML: {e}") from e

        jobs: list[JobBase] = []
        seen_ids: set[str] = set()

        for link in soup.find_all("a", href=True):
            href = link["href"]
            match = _JOB_URL_RE.match(href)
            if match is None:
                continue
            job_id = match.group("id")
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            # Title : prefer h2/h3 inside link, else text
            title_el = link.find(["h1", "h2", "h3", "h4"])
            title = (title_el.get_text(strip=True) if title_el else
                     link.get_text(strip=True))
            if not title or len(title) < 4:
                continue

            # Location: souvent dans un span sibling (TeamTailor pattern)
            location: str | None = None
            for el in link.find_all(["span", "div"], limit=20):
                text = el.get_text(strip=True)
                if any(c in text.lower() for c in _BE_CITIES + ("belgium", "luxembourg")):
                    location = text[:80]
                    break

            country = _country_from_location(location)
            url = (
                href if href.startswith("http")
                else f"https://jobs.orangecyberdefense.com{href}"
            )

            description = title
            if location:
                description = f"{title}. Location: {location}."

            jobs.append(
                JobBase(
                    source=JobSource.ORANGE_CYBERDEFENSE,
                    external_id=job_id,
                    title=title,
                    company=_DEFAULT_COMPANY,
                    location=location or "Unknown",
                    country=country,
                    description=description,
                    url=url,
                    raw_data={"href": href, "slug": match.group("slug")},
                )
            )

        logger.info("[{}] {} unique jobs from listing", self.name, len(jobs))
        # Enrichit avec descriptions complètes depuis les pages détail
        jobs = self._enrich_descriptions(jobs, _DETAIL_SELECTORS)
        return jobs, False
