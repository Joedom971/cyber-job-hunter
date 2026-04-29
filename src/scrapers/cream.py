"""Scraper Cream by Audensiel — ESN cyber Luxembourg.

Cream a quitté `creamconsulting.be` (rebrand vers `creamconsulting.com`).
Page jobs : https://www.creamconsulting.com/jobs

Structure observée (recon 2026-04) :
    <h2>CYBERSECURITY ANALYST</h2>
    ...
    <a href="https://creamconsulting.com/project/cybersecurity-analyst/">
        Apply / read more
    </a>
    + un <p>Contract type</p> à proximité.

Le titre est dans `<h2>`, le lien (avec le slug) est dans le parent commun.
Sprint 2 : on parse uniquement le listing (pas le détail page).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_DEFAULT_COMPANY = "Cream by Audensiel"
_DEFAULT_LOCATION = "Luxembourg"  # HQ
_PROJECT_HREF_RE = re.compile(r"https?://[^/]+/project/(?P<slug>[a-z0-9-]+)/?")
_DETAIL_SELECTORS: tuple[str, ...] = (
    "article.project",       # observé en recon : 3331 chars de contenu propre
    "div.entry-content",
    "main article",
)

# Mots-clés "tech" qu'on attend dans un titre Cream — sert à filtrer les h2
# qui ne sont pas des jobs (ex: "WE WANT YOU", "OUR APPROACH", etc.)
_JOB_TITLE_KEYWORDS = (
    "engineer", "consultant", "analyst", "developer", "specialist",
    "architect", "tester", "designer", "manager", "lead", "expert",
    "developper",  # typo trouvée chez Cream sur 1 offre — on accepte
)


class CreamScraper(BaseScraper):
    """Scrape la liste publique d'offres Cream by Audensiel."""

    name: ClassVar[str] = "cream"
    source: ClassVar[JobSource] = JobSource.CREAM

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False

        response = self._http_get(self.config.base_url)
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse Cream HTML: {e}") from e

        jobs: list[JobBase] = []
        seen_slugs: set[str] = set()

        for h2 in soup.find_all("h2"):
            title = h2.get_text(strip=True)
            if not _is_job_title(title):
                continue

            # Cherche le lien /project/<slug> dans le parent commun
            parent = h2.find_parent()
            link = None
            search_root = parent
            for _ in range(3):  # remonte au max 3 niveaux pour trouver le lien
                if search_root is None:
                    break
                link = search_root.find("a", href=_PROJECT_HREF_RE)
                if link is not None:
                    break
                search_root = search_root.find_parent()

            if link is None:
                continue

            href = link["href"]
            match = _PROJECT_HREF_RE.search(href)
            if match is None:
                continue
            slug = match.group("slug")
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            jobs.append(
                JobBase(
                    source=JobSource.CREAM,
                    external_id=slug,
                    title=title,
                    company=_DEFAULT_COMPANY,
                    location=_DEFAULT_LOCATION,
                    country=Country.LU,
                    description=f"{title}. Cream by Audensiel — ESN Luxembourg.",
                    url=href,
                    raw_data={"slug": slug, "page_url": self.config.base_url},
                )
            )

        logger.info("[{}] {} job titles parsed", self.name, len(jobs))
        jobs = self._enrich_descriptions(jobs, _DETAIL_SELECTORS)
        return jobs, False


def _is_job_title(text: str) -> bool:
    """Heuristique : un h2 d'offre Cream contient un mot-clé de poste tech."""
    if not text or len(text) < 4 or len(text) > 100:
        return False
    lowered = text.lower()
    return any(kw in lowered for kw in _JOB_TITLE_KEYWORDS)


_ = urlparse  # gardé exporté pour debug futur
