"""Scraper EASI — HTML statique sur easi.net/en/jobs.

Structure observée (recon HTTP read-only) :
    <div class="jobs-item jobs__item ...">
        <a class="jobs-item-link" href="/en/jobs/<slug>">
            <h3 class="jobs-item-title">Junior Technical Consultant - Adfinity</h3>
            <div class="jobs-item-offices__location">Ghent, Leuven, Antwerp, Nivelles, Liège</div>
        </a>
    </div>

Sprint 1 : on parse uniquement la liste (titre + URL + locations).
La description complète demanderait de fetch chaque page de détail (35 offres × 3s
= ~2 min). À ajouter en Sprint 2 si le scoring sur titre seul s'avère insuffisant.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_DEFAULT_COMPANY = "EASI"
_DETAIL_SELECTORS: tuple[str, ...] = (
    "main",
    "article",
    "div.entry-content",
)


def _slug_from_href(href: str) -> str:
    """Extrait le slug terminal de '/en/jobs/junior-tech-consultant' → 'junior-tech-consultant'."""
    return href.rstrip("/").rsplit("/", 1)[-1]


def _split_locations(raw: str) -> list[str]:
    """'Ghent, Leuven, Antwerp' → ['Ghent', 'Leuven', 'Antwerp']."""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _pick_primary_location(locations: list[str]) -> str | None:
    """Retourne la 1ère location reconnue prioritaire, sinon la 1ère tout court.

    Préférence : Brussels > Wallonie/LU > la première listée.
    """
    if not locations:
        return None
    preferred_keywords = ("brussels", "bruxelles")
    good_keywords = ("nivelles", "liège", "liege", "luxembourg", "namur", "louvain")
    for loc in locations:
        if any(k in loc.lower() for k in preferred_keywords):
            return loc
    for loc in locations:
        if any(k in loc.lower() for k in good_keywords):
            return loc
    return locations[0]


class EasiScraper(BaseScraper):
    """Scrape la liste publique d'offres EASI."""

    name: ClassVar[str] = "easi"
    source: ClassVar[JobSource] = JobSource.EASI

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            # Toutes les offres sont sur la même page (35 visibles le jour du recon)
            return [], False

        response = self._http_get(self.config.base_url)

        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse EASI HTML: {e}") from e

        items = soup.select("div.jobs-item.jobs__item")
        jobs: list[JobBase] = []
        seen_slugs: set[str] = set()
        for item in items:
            parsed = self._parse_item(item)
            if parsed is None:
                continue
            if parsed.external_id in seen_slugs:
                continue  # déduplication intra-page (au cas où le HTML duplique)
            seen_slugs.add(parsed.external_id)
            jobs.append(parsed)

        logger.info("[{}] HTML had {} items, {} parsed", self.name, len(items), len(jobs))
        jobs = self._enrich_descriptions(jobs, _DETAIL_SELECTORS)
        return jobs, False

    def _parse_item(self, item) -> JobBase | None:  # type: ignore[no-untyped-def]
        link = item.select_one("a.jobs-item-link")
        title_el = item.select_one("h3.jobs-item-title")
        location_el = item.select_one("div.jobs-item-offices__location")

        if link is None or title_el is None:
            return None

        href = link.get("href")
        if not href:
            return None

        title = title_el.get_text(strip=True)
        if not title:
            return None

        slug = _slug_from_href(href)
        url = urljoin(self.config.base_url, href)

        locations = _split_locations(location_el.get_text(strip=True)) if location_el else []
        primary_location = _pick_primary_location(locations)

        # Sprint 1 : on injecte les locations dans la description pour que le scoring
        # voie tous les sites (Brussels parfois en 3e position dans la liste, etc.)
        description = (
            f"{title}. Locations: {', '.join(locations)}." if locations else title
        )

        return JobBase(
            source=JobSource.EASI,
            external_id=slug,
            title=title,
            company=_DEFAULT_COMPANY,
            location=primary_location,
            country=Country.BE,
            description=description,
            url=url,
            raw_data={
                "href": href,
                "all_locations": locations,
                "page_url": self.config.base_url,
            },
        )


_ = urlparse  # utile pour debug futur, gardé exporté
