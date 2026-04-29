"""Scraper KPMG Belgium — RSS feed via TalentSoft.

KPMG expose tous ses postes Belgique via un feed RSS public :
    https://kpmg-career.talent-soft.com/handlers/offerRss.ashx?LCID=2057

Format observé :
    <item>
      <title>2025-1394 - Cloud Security Specialist</title>
      <link>https://kpmg-career.talent-soft.com/Pages/Offre/detailoffre.aspx?idOffre=1394&...</link>
      <description>HTML avec Function/Contract/Position</description>
      <category>Advisory/Senior Advisor</category>
      <category>Permanent</category>
      <category>Luchthaven Brussel Nationaal 1K 1930 Zaventem</category>
    </item>

LCID=2057 = English. Disponibles aussi 1036 (FR) et 1043 (NL) — on prend EN
pour homogénéité avec les descriptions.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar

import feedparser
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError, clean_html_to_text


_DEFAULT_COMPANY = "KPMG Belgium"

# Pattern : "2026-1394 - Cloud Security Specialist" → on garde la partie après " - "
_TITLE_PREFIX_RE = re.compile(r"^(?:\d{4})?-?\d+\s*-\s*")
# Pattern reference dans l'URL : ?idOffre=1394
_OFFRE_ID_RE = re.compile(r"idOffre=(\d+)", re.IGNORECASE)

# Mapping ville → Country pour les adresses des bureaux KPMG belges.
# Ordre : commune spécifique avant grandes villes pour éviter que l'adresse
# 'Luchthaven Brussel Nationaal Zaventem' soit étiquetée 'Brussel' au lieu de
# 'Zaventem'.
_KPMG_BE_CITIES = (
    "zaventem",
    "louvain-la-neuve",
    "antwerp", "antwerpen",
    "ghent", "gent", "gand",
    "hasselt",
    "kortrijk", "courtrai",
    "liège", "liege",
    "louvain",
    "tournai", "doornik",
    "brussels", "bruxelles", "brussel",
)


def _clean_title(raw: str) -> str:
    """`2026-1394 - Cloud Security Specialist` → `Cloud Security Specialist`."""
    return _TITLE_PREFIX_RE.sub("", raw, count=1).strip() or raw


def _strip_html(html: str) -> str:
    """Délégué — on conserve le nom pour les imports/tests existants."""
    return clean_html_to_text(html)


def _parse_categories(entry) -> tuple[list[str], str | None]:  # type: ignore[no-untyped-def]
    """Sépare les `<category>` en (job_categories, location_string).

    L'adresse contient typiquement plusieurs mots dont un nom de ville BE.
    """
    raw_cats = [t.term for t in (entry.get("tags") or [])]
    job_cats: list[str] = []
    location: str | None = None
    for cat in raw_cats:
        lower = cat.lower()
        if any(city in lower for city in _KPMG_BE_CITIES):
            location = cat  # full address (ex: 'Luchthaven Brussel Nationaal 1K 1930 Zaventem')
        elif "/" in cat or cat in ("Permanent", "Internship", "Temporary"):
            job_cats.append(cat)
    return job_cats, location


def _extract_short_location(addr: str | None) -> str | None:
    """Trouve la ville BE dans l'adresse complète."""
    if not addr:
        return None
    lower = addr.lower()
    for city in _KPMG_BE_CITIES:
        if city in lower:
            return city.title()
    return addr


def _parse_pub_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # feedparser exposes pubDate in entry.published_parsed too — but RSS RFC822
        from email.utils import parsedate_to_datetime  # noqa: PLC0415

        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


class KpmgScraper(BaseScraper):
    """Scraper RSS KPMG Belgium (TalentSoft)."""

    name: ClassVar[str] = "kpmg"
    source: ClassVar[JobSource] = JobSource.KPMG

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False  # Le RSS sort tout en 1 fois

        response = self._http_get(self.config.base_url)
        try:
            feed = feedparser.parse(response.text)
        except Exception as e:
            raise ScrapeError(f"feedparser failed: {e}") from e

        if feed.bozo and not feed.entries:
            raise ScrapeError(
                f"KPMG RSS parse error: {getattr(feed, 'bozo_exception', 'unknown')}"
            )

        jobs: list[JobBase] = []
        for entry in feed.entries:
            parsed = self._parse_entry(entry)
            if parsed is not None:
                jobs.append(parsed)

        logger.info(
            "[{}] received {} entries, {} parsed",
            self.name, len(feed.entries), len(jobs),
        )
        return jobs, False

    def _parse_entry(self, entry) -> JobBase | None:  # type: ignore[no-untyped-def]
        link = entry.get("link") or ""
        match = _OFFRE_ID_RE.search(link)
        if match is None:
            return None
        offre_id = match.group(1)

        raw_title = entry.get("title") or ""
        if not raw_title:
            return None
        title = _clean_title(raw_title)

        description_html = entry.get("description") or entry.get("summary") or ""
        description = _strip_html(description_html)

        job_cats, full_address = _parse_categories(entry)
        location = _extract_short_location(full_address)
        posted_at = _parse_pub_date(entry.get("published"))

        # Ajoute les catégories à la description pour aider le scoring
        if job_cats:
            description = f"{description}\n\nCategories: {' | '.join(job_cats)}"

        return JobBase(
            source=JobSource.KPMG,
            external_id=offre_id,
            title=title,
            company=_DEFAULT_COMPANY,
            location=location or "Brussels",
            country=Country.BE,
            description=description,
            url=link,
            posted_at=posted_at,
            raw_data={
                "raw_title": raw_title,
                "categories": job_cats,
                "full_address": full_address,
            },
        )
