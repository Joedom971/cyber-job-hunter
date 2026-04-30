"""Scraper EPAM Belgium — Next.js `_next/data` API.

EPAM héberge son site careers en Next.js. Les données de page sont
disponibles via deux mécanismes :

1. La page HTML embarque `<script id="__NEXT_DATA__">` qui contient le
   `buildId` courant (changé à chaque déploiement EPAM).
2. L'endpoint `_next/data/{buildId}/en/jobs/belgium.json?slug=belgium`
   renvoie le JSON `pageProps` avec les jobs filtrés par pays.

Le `buildId` rotates donc à chaque déploiement. Le scraper extrait
dynamiquement le buildId à chaque run pour rester résilient.

Volume Belgium = ~3 jobs (toutes catégories). Le gate `not_cyber_relevant`
filtrera les généralistes ; les jobs réellement cyber passeront.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any, ClassVar

from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError, clean_html_to_text


_DEFAULT_COMPANY = "EPAM"
_HOMEPAGE_URL = "https://careers.epam.com/en/jobs/belgium"
_DATA_URL_TEMPLATE = (
    "https://careers.epam.com/_next/data/{build_id}/en/jobs/belgium.json?slug=belgium"
)
_DETAIL_URL_BASE = "https://careers.epam.com"

# EPAM CloudFront sert une version cachée stale du __NEXT_DATA__ (buildId
# périmé) quand on appelle avec un UA bot. Avec un UA navigateur, on
# récupère la version courante. On force un UA navigateur sur toutes les
# requêtes du scraper EPAM uniquement.
_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_BUILD_ID_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
    re.DOTALL,
)

# Mapping country name → enum (depuis `country[].name` du JSON EPAM)
_COUNTRY_MAP: dict[str, Country] = {
    "belgium": Country.BE,
    "luxembourg": Country.LU,
    "france": Country.FR,
    "netherlands": Country.NL,
    "germany": Country.DE,
    "ireland": Country.IE,
    "uk": Country.OTHER,
    "united kingdom": Country.OTHER,
    "switzerland": Country.OTHER,
}


def _resolve_country(countries: list[dict[str, Any]] | None) -> Country:
    """Choisit le pays canonique. BE/LU prioritaires (jobs multi-pays cyber).

    Si la liste contient Belgium/Luxembourg, on retourne BE/LU même si
    d'autres pays apparaissent (le poste est ouvert depuis BE).
    """
    if not countries:
        return Country.OTHER
    names = [(c.get("name") or "").lower() for c in countries if isinstance(c, dict)]
    for n in names:
        if n in ("belgium",):
            return Country.BE
    for n in names:
        if n in ("luxembourg",):
            return Country.LU
    for n in names:
        if n in _COUNTRY_MAP:
            return _COUNTRY_MAP[n]
    return Country.OTHER


def _resolve_city(cities: list[dict[str, Any]] | None) -> str:
    if not cities:
        return "-"
    parts = [c.get("name") for c in cities if isinstance(c, dict) and c.get("name")]
    return ", ".join(parts) if parts else "-"


class EpamScraper(BaseScraper):
    """Scraper EPAM Belgium via Next.js _next/data API."""

    name: ClassVar[str] = "epam"
    source: ClassVar[JobSource] = JobSource.EPAM

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False

        # Étape 1 : extraire le buildId depuis la homepage
        build_id = self._fetch_build_id()
        logger.info("[{}] resolved buildId={}", self.name, build_id)

        # Étape 2 : appeler l'endpoint Next.js data
        data_url = _DATA_URL_TEMPLATE.format(build_id=build_id)
        response = self._http_get(
            data_url,
            headers={**_BROWSER_HEADERS, "x-nextjs-data": "1", "Accept": "*/*"},
        )

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as e:
            raise ScrapeError(f"EPAM data endpoint returned invalid JSON: {e}") from e

        section = payload.get("pageProps", {}).get("jobs", {}) or {}
        items = section.get("jobs") or []
        total = section.get("total") or 0

        jobs: list[JobBase] = []
        for item in items:
            parsed = self._parse_item(item)
            if parsed is not None:
                jobs.append(parsed)

        logger.info(
            "[{}] {} parsed (total={}, raw={})",
            self.name, len(jobs), total, len(items),
        )
        return jobs, False

    def _fetch_build_id(self) -> str:
        """Extrait le buildId Next.js depuis la page jobs/belgium.

        Force un UA navigateur : EPAM CloudFront sert une version cachée
        stale (buildId obsolète) avec les UA bot, et le `_next/data/{old}/`
        renvoie 404.
        """
        response = self._http_get(_HOMEPAGE_URL, headers=_BROWSER_HEADERS)
        match = _BUILD_ID_RE.search(response.text)
        if match is None:
            raise ScrapeError("EPAM __NEXT_DATA__ block not found on homepage")
        try:
            data = json.loads(match.group(1))
        except (ValueError, json.JSONDecodeError) as e:
            raise ScrapeError(f"EPAM __NEXT_DATA__ JSON parse failed: {e}") from e
        build_id = data.get("buildId")
        if not build_id or not isinstance(build_id, str):
            raise ScrapeError("EPAM buildId missing from __NEXT_DATA__")
        return build_id

    def _parse_item(self, item: dict[str, Any]) -> JobBase | None:
        uid = (item.get("uid") or "").strip()
        title = (item.get("name") or "").strip()
        if not uid or not title:
            return None

        cities = item.get("city") or []
        countries = item.get("country") or []
        location = _resolve_city(cities)
        country = _resolve_country(countries)

        # URL canonique → seo.url, sinon construit depuis uid
        seo = item.get("seo") or {}
        seo_url = seo.get("url") if isinstance(seo, dict) else None
        url = (
            f"{_DETAIL_URL_BASE}{seo_url}" if seo_url
            else f"{_DETAIL_URL_BASE}/en/vacancy/{uid}_en"
        )

        # Description : déjà dans le JSON (HTML), on strip
        html_desc = item.get("description") or ""
        description = clean_html_to_text(html_desc) if html_desc else title

        return JobBase(
            source=JobSource.EPAM,
            external_id=uid,
            title=title,
            company=_DEFAULT_COMPANY,
            location=location,
            country=country,
            description=description[:8000],
            url=url,
            raw_data={
                "unique_id": item.get("unique_id"),
                "seniority": item.get("seniority"),
                "vacancy_type": item.get("vacancy_type"),
                "skills": item.get("skills"),
                "primary_skill": item.get("primary_skill"),
                "tags": item.get("tags"),
                "tenant": item.get("tenant"),
                "countries_raw": [c.get("name") for c in countries if isinstance(c, dict)],
                "created_at": item.get("created_at"),
            },
        )
