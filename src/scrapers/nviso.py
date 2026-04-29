"""Scraper NVISO — HTML statique sur nviso.eu/jobs/.

⚠️ NVISO a quitté Recruitee (l'API renvoie 404 depuis avril 2026).
Les offres sont maintenant servies en SSR Next.js sur leur site.

Structure observée (recon HTTP read-only) :
    <a href="/job/<slug>">
        <h3>Title</h3>
        <div>Country</div>   <!-- ex. 'Belgium', 'Greece', 'Germany' -->
        <div>Apply now ...</div>  <!-- bouton, à ignorer -->
    </a>

Les classes CSS sont des utility Tailwind (sans sémantique) → on s'appuie
exclusivement sur le sélecteur d'attribut `a[href^="/job/"]` et la position
des éléments enfants.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_DEFAULT_COMPANY = "NVISO"

# Country names servis par NVISO (constatés au recon) → enum
_COUNTRY_NAME_MAP: dict[str, Country] = {
    "belgium": Country.BE,
    "luxembourg": Country.LU,
    "germany": Country.DE,
    "france": Country.FR,
    "netherlands": Country.NL,
    "ireland": Country.IE,
    "online": Country.REMOTE,
    "remote": Country.REMOTE,
    # NVISO a aussi Greece et Austria, qu'on classe en OTHER
    # car non couverts par notre profil (mais on garde le label dans `location`)
}


def _slug_from_href(href: str) -> str:
    """`/job/junior-cyber-strategy-be` → `junior-cyber-strategy-be`."""
    return href.rstrip("/").rsplit("/", 1)[-1]


def _extract_location_text(link_element) -> str | None:  # type: ignore[no-untyped-def]
    """Premier `<div>` direct dont le texte est court (≤ 30 chars) et ne ressemble pas à un bouton.

    Heuristique stable même si NVISO change ses classes Tailwind.
    """
    for div in link_element.find_all("div", recursive=False):
        text = div.get_text(strip=True)
        if not text or len(text) > 30:
            continue
        lowered = text.lower()
        if lowered.startswith("apply") or "now" in lowered:
            continue
        return text
    return None


class NvisoScraper(BaseScraper):
    """Scrape la liste publique d'offres NVISO (cyber pure-player BE)."""

    name: ClassVar[str] = "nviso"
    source: ClassVar[JobSource] = JobSource.NVISO

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False  # Toutes les offres servies sur la même page

        response = self._http_get(self.config.base_url)
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse NVISO HTML: {e}") from e

        links = soup.select('a[href^="/job/"]')
        jobs: list[JobBase] = []
        seen: set[str] = set()
        for link in links:
            parsed = self._parse_link(link)
            if parsed is None:
                continue
            if parsed.external_id in seen:
                continue
            seen.add(parsed.external_id)
            jobs.append(parsed)

        logger.info(
            "[{}] HTML had {} job links, {} parsed",
            self.name, len(links), len(jobs),
        )
        return jobs, False

    def _parse_link(self, link) -> JobBase | None:  # type: ignore[no-untyped-def]
        href = link.get("href")
        if not href:
            return None
        h3 = link.find("h3")
        if h3 is None:
            return None
        title = h3.get_text(strip=True)
        if not title:
            return None

        slug = _slug_from_href(href)
        url = urljoin(self.config.base_url, href)

        location_text = _extract_location_text(link)
        country = _COUNTRY_NAME_MAP.get(
            (location_text or "").lower(), Country.OTHER
        )

        # Le listing seul ne donne pas de description → on construit un texte minimal
        # pour que le scoring puisse au moins matcher le titre + la location.
        description = (
            f"{title}. Location: {location_text}." if location_text else title
        )

        return JobBase(
            source=JobSource.NVISO,
            external_id=slug,
            title=title,
            company=_DEFAULT_COMPANY,
            location=location_text,
            country=country,
            description=description,
            url=url,
            raw_data={"href": href, "page_url": self.config.base_url},
        )
