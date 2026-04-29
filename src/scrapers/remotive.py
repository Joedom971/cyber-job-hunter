"""Scraper Remotive — API REST JSON publique.

⚠️ TOS Remotive (https://remotive.com/api-documentation) :
    - Max 4 requêtes / jour (géré par `rate_limit_seconds: 21600` dans sources.yaml)
    - Données délaiées de 24h
    - Attribution obligatoire : "Source: Remotive" + lien retour
    - Pas de republication tierce

L'API ne propose pas de catégorie 'cybersecurity'. On scrape `software-dev`
et on laisse le scoring (mots-clés cyber) filtrer côté Python.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, ClassVar

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


# Catégories Remotive utiles pour notre filtre cyber.
# Plus large = plus de bruit mais meilleure coverage. Les TOS limitent à 4 req/j ;
# on en fait 1 par run avec la catégorie la plus pertinente.
DEFAULT_CATEGORY = "software-dev"


def _strip_html(html_text: str) -> str:
    """Convertit du HTML basique en texte brut. Préserve les sauts de ligne logiques."""
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    return soup.get_text(separator="\n").strip()


def _map_country(raw_location: str) -> tuple[Country, str | None]:
    """Mapping heuristique du champ `candidate_required_location`.

    Retourne (country_enum, location_str_propre).
    """
    if not raw_location:
        return Country.REMOTE, None

    loc_lower = raw_location.lower()
    keyword_to_country: tuple[tuple[str, Country], ...] = (
        ("belgium", Country.BE),
        ("brussels", Country.BE),
        ("luxembourg", Country.LU),
        ("netherlands", Country.NL),
        ("france", Country.FR),
        ("germany", Country.DE),
        ("ireland", Country.IE),
    )
    for kw, country in keyword_to_country:
        if kw in loc_lower:
            return country, raw_location

    if loc_lower in ("worldwide", "anywhere", "remote", "europe"):
        return Country.REMOTE, raw_location

    return Country.OTHER, raw_location


def _parse_publication_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # Format Remotive : "2026-04-24T10:11:12" (parfois sans Z)
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.debug("[remotive] cannot parse publication_date: {!r}", raw)
        return None


class RemotiveScraper(BaseScraper):
    """Scraper Remotive — 1 appel API JSON, pas de pagination."""

    name: ClassVar[str] = "remotive"
    source: ClassVar[JobSource] = JobSource.REMOTIVE

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        # API renvoie tout d'un coup, ignore les pages > 1
        if page > 1:
            return [], False

        response = self._http_get(self.config.base_url, params={"category": DEFAULT_CATEGORY})
        try:
            data: dict[str, Any] = response.json()
        except ValueError as e:
            raise ScrapeError(f"Remotive returned invalid JSON: {e}") from e

        raw_items = data.get("jobs") or []
        jobs: list[JobBase] = []
        for item in raw_items:
            parsed = self._parse_item(item)
            if parsed is not None:
                jobs.append(parsed)

        logger.info(
            "[{}] received {} raw items, {} parsed (total-job-count={})",
            self.name, len(raw_items), len(jobs), data.get("total-job-count"),
        )
        return jobs, False

    def _parse_item(self, item: dict[str, Any]) -> JobBase | None:
        """Convertit 1 entrée brute Remotive en `JobBase`. Retourne None si invalide."""
        try:
            ext_id = str(item["id"])
            title = item["title"]
            url = item["url"]
        except KeyError as e:
            logger.warning("[{}] missing required field {}, skipping", self.name, e)
            return None

        company = item.get("company_name") or "Unknown"
        country, location = _map_country(item.get("candidate_required_location") or "")
        description = _strip_html(item.get("description") or "")
        posted_at = _parse_publication_date(item.get("publication_date"))

        return JobBase(
            source=JobSource.REMOTIVE,
            external_id=ext_id,
            title=title,
            company=company,
            location=location,
            country=country,
            description=description,
            url=url,
            posted_at=posted_at,
            raw_data=item,  # payload complet conservé pour debug
        )
