"""Scraper Devoteam — HTML statique sur devoteam.com/jobs/.

Devoteam est une ESN multi-pays (FR/BE/LU + autres). La page jobs liste
toutes les offres avec un pattern URL clair :
    https://www.devoteam.com/jobs/{slug}-{numeric-id-15-chars}

Le titre est dans un <h2> à l'intérieur du lien. La localisation n'est
pas exposée sur le listing — elle vit sur la page détail. Sprint 2+ :
on enrichit via _enrich_descriptions pour récupérer titre + location +
description complète.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_DEFAULT_COMPANY = "Devoteam"
_DETAIL_SELECTORS: tuple[str, ...] = (
    "main article",
    "article",
    "main",
    "div.entry-content",
)
_JOB_URL_RE = re.compile(
    r"^https?://(?:www\.)?devoteam\.com/jobs/(?P<slug>[a-z0-9-]+?)-(?P<id>\d{12,})/?$"
)
_BE_LU_HINTS = (
    "brussels", "bruxelles", "antwerp", "luxembourg",
    "belgium", "belgique", "luxembourg",
)


def _country_from_text(text: str) -> Country:
    lower = (text or "").lower()
    if "luxembourg" in lower:
        return Country.LU
    if any(c in lower for c in _BE_LU_HINTS):
        return Country.BE
    if "france" in lower or "paris" in lower or "lyon" in lower:
        return Country.FR
    if "netherlands" in lower or "amsterdam" in lower:
        return Country.NL
    return Country.OTHER


class DevoteamScraper(BaseScraper):
    """Scrape la liste publique d'offres Devoteam."""

    name: ClassVar[str] = "devoteam"
    source: ClassVar[JobSource] = JobSource.DEVOTEAM

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False

        response = self._http_get(self.config.base_url)
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse Devoteam HTML: {e}") from e

        jobs: list[JobBase] = []
        seen_ids: set[str] = set()

        for link in soup.find_all("a", href=True):
            match = _JOB_URL_RE.match(link["href"])
            if match is None:
                continue
            job_id = match.group("id")
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            slug = match.group("slug")
            title_el = link.find(["h2", "h3", "h4"])
            title = (title_el.get_text(strip=True) if title_el
                     else link.get_text(strip=True))
            if not title or len(title) < 5:
                continue
            # Sometimes Devoteam appends ", Permanent contract" — clean it
            title = re.sub(r",\s*(Permanent|Temporary|Contract|CDI|CDD).*$", "", title, flags=re.I).strip()

            jobs.append(
                JobBase(
                    source=JobSource.DEVOTEAM,
                    external_id=job_id,
                    title=title,
                    company=_DEFAULT_COMPANY,
                    location="Unknown",  # rempli après enrichissement
                    country=Country.OTHER,  # idem
                    description=title,
                    url=link["href"],
                    raw_data={"slug": slug, "raw_text": link.get_text(strip=True)[:300]},
                )
            )

        logger.info("[{}] {} unique jobs from listing", self.name, len(jobs))
        # Devoteam bloque notre User-Agent honnête sur les pages détail (403).
        # On reste sur le listing seul. Le scoring travaille avec le titre
        # seul → certaines offres cyber évidentes passent, d'autres non.
        # On enrichit la description avec le slug pour aider le matching
        # (ex: "consultant-securite-senior-vulnerabilite-management-cti").
        for j in jobs:
            slug = j.raw_data.get("slug", "")
            slug_text = slug.replace("-", " ")
            j.description = f"{j.title}. Keywords from URL: {slug_text}"
            j.country = _country_from_text(j.description)
        return jobs, False
