"""Scraper Recruitee — utilisé par NVISO, itsme et toute boîte hébergée chez Recruitee.

Recruitee expose une API JSON publique :
    https://{company}.recruitee.com/api/offers/

Format de réponse :
    {"offers": [{
        "id": int, "slug": str, "title": str,
        "city": str, "country": str, "country_code": str,
        "careers_url": str, "description": str (HTML),
        "requirements": str (HTML), "published_at": str, "status": str,
        ...
    }]}

Une seule classe scrape **toutes** les boîtes Recruitee : on la paramètre via
`base_url` dans `sources.yaml` et `source` injecté dynamiquement.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, ClassVar

from loguru import logger

from src.config import SourceConfig
from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError
from src.scrapers.remotive import _strip_html  # réutilisable, identique


_COUNTRY_CODE_MAP: dict[str, Country] = {
    "BE": Country.BE,
    "LU": Country.LU,
    "FR": Country.FR,
    "NL": Country.NL,
    "DE": Country.DE,
    "IE": Country.IE,
}


def _parse_recruitee_date(raw: str | None) -> datetime | None:
    """Format Recruitee : '2026-04-28 20:47:28 UTC'."""
    if not raw:
        return None
    try:
        cleaned = raw.replace(" UTC", "+00:00").replace(" ", "T", 1)
        return datetime.fromisoformat(cleaned)
    except ValueError:
        try:
            # Fallback : le timestamp est parfois en ISO direct
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            logger.debug("[recruitee] cannot parse date: {!r}", raw)
            return None


class RecruiteeScraper(BaseScraper):
    """Scraper paramétrable. Accepte la `source` à l'instanciation pour les variantes."""

    name: ClassVar[str] = "recruitee"
    source: ClassVar[JobSource] = JobSource.OTHER  # override par instance

    def __init__(
        self,
        config: SourceConfig,
        source: JobSource,
        company_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        # Override la ClassVar par une instance var (seulement pour CETTE instance)
        self.source = source  # type: ignore[misc]
        self.name = source.value
        self._company_name = company_name or config.company_name_override

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False  # API renvoie tout d'un coup

        response = self._http_get(self.config.base_url)
        try:
            data: dict[str, Any] = response.json()
        except ValueError as e:
            raise ScrapeError(f"Recruitee returned invalid JSON: {e}") from e

        raw_offers = data.get("offers") or []
        jobs: list[JobBase] = []
        for offer in raw_offers:
            parsed = self._parse_offer(offer)
            if parsed is not None:
                jobs.append(parsed)

        logger.info(
            "[{}] received {} offers, {} parsed",
            self.name, len(raw_offers), len(jobs),
        )
        return jobs, False

    def _parse_offer(self, offer: dict[str, Any]) -> JobBase | None:
        try:
            ext_id = str(offer["id"])
            title = offer["title"]
        except KeyError as e:
            logger.warning("[{}] missing field {}, skipping", self.name, e)
            return None

        slug = offer.get("slug") or ""
        url = offer.get("careers_url") or self._build_fallback_url(slug)
        if not url:
            logger.warning("[{}] offer {} has no URL, skipping", self.name, ext_id)
            return None

        company = (
            self._company_name
            or offer.get("company_name")
            or offer.get("department")
            or "Unknown"
        )

        country_code = (offer.get("country_code") or "").upper()
        country = _COUNTRY_CODE_MAP.get(country_code, Country.OTHER)

        # Concat title + description + requirements pour scoring
        description_parts = []
        if d := offer.get("description"):
            description_parts.append(_strip_html(d))
        if r := offer.get("requirements"):
            description_parts.append(_strip_html(r))
        description = "\n\n".join(description_parts)

        location = offer.get("city") or offer.get("location") or None
        posted_at = _parse_recruitee_date(
            offer.get("published_at") or offer.get("created_at")
        )

        # Filtre les offres non publiées
        if (offer.get("status") or "").lower() not in ("published", ""):
            return None

        return JobBase(
            source=self.source,
            external_id=ext_id,
            title=title,
            company=company,
            location=location,
            country=country,
            description=description,
            url=url,
            posted_at=posted_at,
            raw_data=offer,
        )

    def _build_fallback_url(self, slug: str) -> str | None:
        """Si careers_url manque, on en construit un depuis base_url + slug."""
        if not slug:
            return None
        # base_url = https://nviso.recruitee.com/api/offers/
        host = self.config.base_url.split("/api/")[0]
        return f"{host}/o/{slug}"


# ─── Factories pré-paramétrées (utilisées par le runner) ─────────────────


def build_nviso_scraper(config: SourceConfig, **kwargs: Any) -> RecruiteeScraper:
    return RecruiteeScraper(
        config, source=JobSource.NVISO, company_name="NVISO", **kwargs
    )


def build_itsme_scraper(config: SourceConfig, **kwargs: Any) -> RecruiteeScraper:
    return RecruiteeScraper(
        config, source=JobSource.ITSME, company_name="itsme", **kwargs
    )


__all__ = [
    "RecruiteeScraper",
    "build_itsme_scraper",
    "build_nviso_scraper",
]


# Garde une référence vers `datetime` et `timezone` pour les tests d'import
_ = (datetime, timezone)
