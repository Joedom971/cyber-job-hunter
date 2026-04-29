"""Scraper Capgemini Belgium — API JSON propriétaire (Azure-hosted).

Capgemini héberge son propre job board sur Azure :
    GET https://cg-jobstream-api.azurewebsites.net/api/job-search
    ?page=1&size=20&search=cyber&country_code=be-en

Headers requis :
    Origin: https://www.capgemini.com   (CORS check côté API)

Format de réponse :
    {
      "count": N,  "total": N,
      "data": [{
          "id": str,  "ref": str,
          "title": str,
          "description_stripped": str (HTML déjà strippé),
          "country_code": "be-en",  "country_name": "Belgium",
          "location": str (ville),
          "brand": str,  "experience_level": str,  "education_level": str,
          ...
      }]
    }
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar

from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_DEFAULT_COMPANY = "Capgemini"
_PAGE_SIZE = 20
_COUNTRY_CODE_BE = "be-en"
_API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.capgemini.com",
    "Sec-Fetch-Mode": "cors",
}


class CapgeminiScraper(BaseScraper):
    """Scraper Capgemini via leur API job-search."""

    name: ClassVar[str] = "capgemini"
    source: ClassVar[JobSource] = JobSource.CAPGEMINI

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        params = {
            "page": page,
            "size": _PAGE_SIZE,
            "search": "cyber",
            "country_code": _COUNTRY_CODE_BE,
        }
        try:
            response = self._client.get(
                self.config.base_url, params=params, headers=_API_HEADERS
            )
        except Exception as e:  # noqa: BLE001
            raise ScrapeError(f"Capgemini API request failed: {e}") from e

        if not (200 <= response.status_code < 300):
            raise ScrapeError(
                f"Capgemini returned {response.status_code} for {response.url}"
            )

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as e:
            raise ScrapeError(f"Capgemini returned invalid JSON: {e}") from e

        items = payload.get("data") or []
        total = payload.get("total") or 0

        jobs: list[JobBase] = []
        for item in items:
            parsed = self._parse_item(item)
            if parsed is not None:
                jobs.append(parsed)

        has_next = (page * _PAGE_SIZE) < total
        logger.info(
            "[{}] page {}: {} parsed (total={}, has_next={})",
            self.name, page, len(jobs), total, has_next,
        )
        return jobs, has_next

    def _parse_item(self, item: dict[str, Any]) -> JobBase | None:
        ext_id = str(item.get("id") or item.get("ref") or "").strip()
        title = (item.get("title") or "").strip()
        if not ext_id or not title:
            return None

        ref = item.get("ref") or ext_id
        description = item.get("description_stripped") or item.get("description") or ""
        location = item.get("location") or "Brussels"
        brand = item.get("brand") or _DEFAULT_COMPANY

        # URL canonique candidate (Capgemini → /careers/job/{ref})
        url = f"https://www.capgemini.com/be-en/jobs/{ref}/"

        return JobBase(
            source=JobSource.CAPGEMINI,
            external_id=ext_id,
            title=title,
            company=brand,
            location=location,
            country=Country.BE,
            description=description,
            url=url,
            raw_data={
                "ref": ref,
                "experience_level": item.get("experience_level"),
                "education_level": item.get("education_level"),
                "contract_type": item.get("contract_type"),
                "country_name": item.get("country_name"),
            },
        )
