"""Scraper Actiris — service public emploi Bruxelles.

Actiris expose un sitemap public de 9000+ offres :
    https://www.actiris.brussels/sitemapoffers-fr.xml

Stratégie :
1. On fetch le sitemap (lastmod par offre)
2. On trie par lastmod descendant → offres les plus récentes d'abord
3. On limite à `max_pages × PAGE_SIZE` offres (par défaut 40)
4. On fetch la page détail de chacune pour extraire titre + locations

Format URL d'une offre :
    https://www.actiris.brussels/fr/citoyens/detail-offre-d-emploi/?reference=NNNN

Format `<title>` :
    "Title H/F/X - Ref. NNNN | Actiris"

Format `og:description` :
    "Title - Ref NNNN - Belgique - <Ville> - <Type>"
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


SITEMAP_URL = "https://www.actiris.brussels/sitemapoffers-fr.xml"
_DEFAULT_COMPANY = "Actiris (offre publique)"
_PAGE_SIZE = 20

_SITEMAP_ENTRY_RE = re.compile(
    r"<url>\s*<loc>([^<]+reference=(\d+)[^<]*)</loc>\s*<lastmod>([^<]+)</lastmod>",
    re.IGNORECASE | re.DOTALL,
)
_TITLE_CLEAN_RE = re.compile(r"^(.+?)\s*-\s*Ref\.?\s*\d+\b", re.IGNORECASE)


def _clean_title(raw: str) -> str:
    """Strip ' - Ref. NNN | Actiris' du title pour ne garder que le poste."""
    raw = raw.replace(" | Actiris", "").strip()
    match = _TITLE_CLEAN_RE.match(raw)
    return match.group(1).strip() if match else raw


def _parse_og_description(og: str | None) -> tuple[str | None, str | None]:
    """Format observé : 'Title - Ref NNN - Belgique - <Ville> - <Type>'.

    Retourne (location_ville, type_contrat). Country est toujours BE pour Actiris.
    """
    if not og:
        return None, None
    parts = [p.strip() for p in og.split(" - ")]
    if len(parts) < 4:
        return None, None
    # parts[0]=titre, [1]=Ref X, [2]=country, [3]=ville, [4]=type (optionnel)
    location = parts[3] if len(parts) > 3 else None
    job_type = parts[4] if len(parts) > 4 else None
    return location, job_type


def _parse_iso_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class ActirisScraper(BaseScraper):
    """Scrape les offres Actiris via sitemap + pages détail."""

    name: ClassVar[str] = "actiris"
    source: ClassVar[JobSource] = JobSource.ACTIRIS

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._sitemap_entries: list[dict[str, str]] | None = None

    def _fetch_sitemap(self) -> list[dict[str, str]]:
        """Fetch + parse le sitemap. Cache en mémoire pour pages suivantes."""
        if self._sitemap_entries is not None:
            return self._sitemap_entries

        response = self._http_get(SITEMAP_URL)
        entries: list[dict[str, str]] = []
        for match in _SITEMAP_ENTRY_RE.finditer(response.text):
            url = match.group(1)
            ref = match.group(2)
            lastmod = match.group(3)
            entries.append({"url": url, "ref": ref, "lastmod": lastmod})

        # Trie par lastmod desc — offres modifiées le plus récemment d'abord
        entries.sort(key=lambda e: e["lastmod"], reverse=True)

        logger.info("[{}] sitemap parsed: {} offers", self.name, len(entries))
        self._sitemap_entries = entries
        return entries

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        all_entries = self._fetch_sitemap()
        start = (page - 1) * _PAGE_SIZE
        end = start + _PAGE_SIZE
        page_entries = all_entries[start:end]
        if not page_entries:
            return [], False

        jobs: list[JobBase] = []
        for entry in page_entries:
            job = self._fetch_detail(entry)
            if job is not None:
                jobs.append(job)

        has_next = end < len(all_entries)
        logger.info(
            "[{}] page {}: {} offers parsed (slice {}-{}, has_next={})",
            self.name, page, len(jobs), start, end, has_next,
        )
        return jobs, has_next

    def _fetch_detail(self, entry: dict[str, str]) -> JobBase | None:
        """Fetch détail page + parse title et og:description."""
        try:
            response = self._http_get(entry["url"])
        except ScrapeError as e:
            logger.warning("[{}] skip ref={}: {}", self.name, entry["ref"], e)
            return None

        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            logger.warning(
                "[{}] HTML parse failed for ref={}: {}",
                self.name, entry["ref"], e,
            )
            return None

        raw_title = soup.title.get_text(strip=True) if soup.title else ""
        title = _clean_title(raw_title) or f"Offre Actiris ref. {entry['ref']}"

        og_desc_meta = soup.find("meta", property="og:description")
        og_desc = og_desc_meta.get("content") if og_desc_meta else None
        location, job_type = _parse_og_description(og_desc)

        # Description visible côté scoring : titre + meta
        description_parts = [title]
        if og_desc:
            description_parts.append(og_desc)
        description = "\n".join(description_parts)

        return JobBase(
            source=JobSource.ACTIRIS,
            external_id=entry["ref"],
            title=title,
            company=_DEFAULT_COMPANY,
            location=location or "Brussels",
            country=Country.BE,
            description=description,
            url=entry["url"],
            posted_at=_parse_iso_date(entry["lastmod"]),
            raw_data={
                "ref": entry["ref"],
                "lastmod": entry["lastmod"],
                "job_type": job_type,
                "og_description": og_desc,
            },
        )
