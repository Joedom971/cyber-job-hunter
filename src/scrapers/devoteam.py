"""Scraper Devoteam — Google Cloud Talent Solution API filtré Belgium.

Devoteam expose son job board via une API Google Cloud Functions hébergée
sur GCP europe-west1. URL et auth Basic publiques (visibles dans le bundle
JS du site Devoteam) :

    GET https://europe-west1-dsi-careers.cloudfunctions.net/careers-api/v1.1
        ?pageSize=15&offset=N&country=Belgium
    Header: Authorization: Basic <base64(public_credentials)>
    Header: Origin: https://www.devoteam.com (CORS check)

Format de réponse (Google Cloud Talent Solution) :
    {
      "totalSize": 49,
      "matchingJobs": [
        {
          "job": {
            "title": "...",
            "addresses": ["Culliganlaan 3 Machelen Belgium"],
            "name": "projects/.../jobs/{jobId}",
            "description": "<HTML>",
            "applicationInfo": {"uris": [...]},
            "qualifications": "...",
            ...
          },
          "jobSummary": "..."
        }
      ]
    }

Avantages vs HTML précédent :
- Filtre BE natif (49 jobs vs 15 mondial)
- Description complète directement dans la réponse
- Pas de blocage UA (l'API n'est pas filtrée)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, ClassVar

from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError, clean_html_to_text


_DEFAULT_COMPANY = "Devoteam"
_PAGE_SIZE = 15
# Auth Basic publique (visible dans le bundle JS Devoteam — pas un secret).
# decoded: lemondechange:hNLFZ7Yd9p3CEmzr
_AUTH_HEADER = "Basic bGVtb25kZWNoYW5nZTpoTkxGWjdZZDlwM0NFbXpy"
_API_HEADERS = {
    "Accept": "application/json",
    "Authorization": _AUTH_HEADER,
    "Content-Type": "application/json",
    "Origin": "https://www.devoteam.com",
    "Sec-Fetch-Mode": "cors",
}

# Pour extraire l'ID du job dans le `name` GCP : projects/.../jobs/{jobId}
_JOB_NAME_RE = re.compile(r"jobs/([^/]+)$")


def _country_from_address(addresses: list[str]) -> tuple[Country, str | None]:
    """Détecte pays + ville depuis la liste d'addresses Google."""
    if not addresses:
        return Country.OTHER, None
    addr = addresses[0]
    lower = addr.lower()
    # Détection pays
    if "luxembourg" in lower:
        country = Country.LU
    elif any(c in lower for c in ("belgium", "belgique", "machelen", "brussels",
                                    "bruxelles", "antwerp", "ghent", "namur",
                                    "louvain", "wavre", "liège")):
        country = Country.BE
    elif any(c in lower for c in ("france", "paris", "lyon", "lille", "nantes",
                                    "marseille", "toulouse", "bordeaux")):
        country = Country.FR
    elif "netherlands" in lower or "amsterdam" in lower:
        country = Country.NL
    else:
        country = Country.OTHER

    # Extraction ville simple : segment avant "Belgium"/"France"/etc.
    cleaned = re.sub(r"^\d+\s+", "", addr)  # retire numéro de rue éventuel
    parts = cleaned.split(",")
    city = parts[-2].strip() if len(parts) >= 2 else parts[0].strip()
    # Fallback : prend le dernier mot avant le pays
    if country.value in city.lower() or len(city) > 60:
        words = cleaned.replace(country.value.title(), "").split()
        if words:
            city = words[-1] if not words[-1].isdigit() else (words[-2] if len(words) > 1 else words[-1])
    return country, city or None


def _strip_html(html: str) -> str:
    """Délégué — on conserve le nom pour les imports/tests existants."""
    return clean_html_to_text(html)


class DevoteamScraper(BaseScraper):
    """Scrape Devoteam Belgium via Google Cloud Talent Solution API."""

    name: ClassVar[str] = "devoteam"
    source: ClassVar[JobSource] = JobSource.DEVOTEAM

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        offset = (page - 1) * _PAGE_SIZE
        params = {
            "pageSize": _PAGE_SIZE,
            "offset": offset,
            "jobProfile": "",
            "infiniteTeam": "",
            "experienceLevel": "",
            "country": "Belgium",
        }
        try:
            response = self._client.get(
                self.config.base_url, params=params, headers=_API_HEADERS
            )
        except Exception as e:  # noqa: BLE001
            raise ScrapeError(f"Devoteam API request failed: {e}") from e

        if not (200 <= response.status_code < 300):
            raise ScrapeError(
                f"Devoteam returned {response.status_code}"
            )

        try:
            data: dict[str, Any] = response.json()
        except ValueError as e:
            raise ScrapeError(f"Devoteam returned invalid JSON: {e}") from e

        items = data.get("matchingJobs") or []
        total = data.get("totalSize") or 0

        jobs: list[JobBase] = []
        for item in items:
            parsed = self._parse_item(item)
            if parsed is not None:
                jobs.append(parsed)

        has_next = (offset + _PAGE_SIZE) < total
        logger.info(
            "[{}] page {}: {} parsed (total={}, has_next={})",
            self.name, page, len(jobs), total, has_next,
        )
        return jobs, has_next

    def _parse_item(self, item: dict[str, Any]) -> JobBase | None:
        job = item.get("job") or item
        title = (job.get("title") or "").strip()
        if not title:
            return None

        # GCP job name : projects/.../jobs/{jobId}
        name = job.get("name") or ""
        match = _JOB_NAME_RE.search(name)
        ext_id = match.group(1) if match else (job.get("requisitionId") or title[:50])
        if not ext_id:
            return None

        addresses = job.get("addresses") or []
        country, city = _country_from_address(addresses)

        description_html = job.get("description") or ""
        qualifications_html = job.get("qualifications") or ""
        responsibilities_html = job.get("responsibilities") or ""

        full_desc = "\n\n".join(
            _strip_html(s) for s in (description_html, qualifications_html, responsibilities_html) if s
        ) or _strip_html(item.get("jobSummary") or "") or title

        # URL canonique : applicationInfo.uris[0]
        app_info = job.get("applicationInfo") or {}
        uris = app_info.get("uris") or []
        url = uris[0] if uris else f"https://www.devoteam.com/jobs/?ref={ext_id}"

        return JobBase(
            source=JobSource.DEVOTEAM,
            external_id=str(ext_id),
            title=title,
            company=_DEFAULT_COMPANY,
            location=city or "Belgium",
            country=country,
            description=full_desc[:8000],
            url=url,
            raw_data={
                "addresses": addresses,
                "requisitionId": job.get("requisitionId"),
                "name": name,
                "jobSummary": item.get("jobSummary", "")[:200],
            },
        )
