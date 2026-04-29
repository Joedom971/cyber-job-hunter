"""Scraper Workday CXS — paramétrable pour tous les sites Workday publics.

Workday expose une API JSON publique commune à tous ses tenants :
    POST https://{tenant}.{cluster}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

Body :
    {"appliedFacets": {...}, "limit": 20, "offset": N, "searchText": "..."}

Une seule classe `WorkdayScraper` paramétrée par `(host, tenant, site, search_text)`
peut scraper Accenture, Proximus (si Workday), Sopra Steria (si Workday), etc.

Sprint 2 polish : on instancie pour Accenture (Big4 conseil cyber).
Le scraper récupère les jobs cyber monde, le scoring filtre par pays après.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, ClassVar

from bs4 import BeautifulSoup
from loguru import logger

from src.config import SourceConfig
from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_PAGE_SIZE = 20

# Mapping pays observés dans les externalPath Accenture (`/job/<City>/<slug>_<id>`).
# La logique : on devine le pays depuis le nom de ville. Sinon Country.OTHER.
_CITY_TO_COUNTRY: dict[str, Country] = {
    # BE
    "brussels": Country.BE, "bruxelles": Country.BE, "antwerp": Country.BE,
    "ghent": Country.BE, "liege": Country.BE, "namur": Country.BE,
    # LU
    "luxembourg": Country.LU,
    # FR
    "paris": Country.FR, "lyon": Country.FR, "marseille": Country.FR,
    "lille": Country.FR, "toulouse": Country.FR, "bordeaux": Country.FR,
    # NL
    "amsterdam": Country.NL, "rotterdam": Country.NL, "utrecht": Country.NL,
    # DE
    "berlin": Country.DE, "munich": Country.DE, "frankfurt": Country.DE,
    "hamburg": Country.DE, "cologne": Country.DE,
    # IE
    "dublin": Country.IE, "cork": Country.IE,
}


_PATH_RE = re.compile(
    r"^/job/(?P<city>[^/]+)/(?P<slug>.+?)_(?P<job_id>[A-Z]?\d{4,}(?:-\d+)?)$"
)


def _city_to_country(city_raw: str) -> Country:
    """Mappe un nom de ville (potentiellement encodé URL) vers Country."""
    cleaned = city_raw.replace("-", " ").replace("%20", " ").lower().strip()
    for keyword, country in _CITY_TO_COUNTRY.items():
        if keyword in cleaned:
            return country
    return Country.OTHER


class WorkdayScraper(BaseScraper):
    """Scraper paramétrable pour les sites Workday publics.

    Sous-classes ou factories doivent fournir `_workday_host`, `_workday_tenant`,
    `_workday_site` (et éventuellement `_workday_search`).
    """

    name: ClassVar[str] = "workday"
    source: ClassVar[JobSource] = JobSource.OTHER  # override par instance

    def __init__(
        self,
        config: SourceConfig,
        source: JobSource,
        host: str,
        tenant: str,
        site: str,
        company_name: str = "Unknown",
        search_text: str = "cyber",
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self.source = source  # type: ignore[misc]
        self.name = source.value
        self._workday_host = host  # ex: "accenture.wd103.myworkdayjobs.com"
        self._workday_tenant = tenant  # ex: "accenture"
        self._workday_site = site  # ex: "AccentureCareers"
        self._company_name = company_name
        self._workday_search = search_text

    @property
    def _api_url(self) -> str:
        return (
            f"https://{self._workday_host}/wday/cxs/"
            f"{self._workday_tenant}/{self._workday_site}/jobs"
        )

    @property
    def _detail_base_url(self) -> str:
        return f"https://{self._workday_host}/{self._workday_site}"

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        offset = (page - 1) * _PAGE_SIZE
        body = {
            "appliedFacets": {},
            "limit": _PAGE_SIZE,
            "offset": offset,
            "searchText": self._workday_search,
        }

        # Workday accepte uniquement POST avec JSON
        response = self._client.post(
            self._api_url,
            json=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if not (200 <= response.status_code < 300):
            raise ScrapeError(
                f"Workday API returned {response.status_code} for {self._api_url}"
            )

        try:
            data: dict[str, Any] = response.json()
        except ValueError as e:
            raise ScrapeError(f"Workday returned invalid JSON: {e}") from e

        raw = data.get("jobPostings") or []
        total = data.get("total") or 0
        jobs: list[JobBase] = []
        for posting in raw:
            parsed = self._parse_posting(posting)
            if parsed is not None:
                jobs.append(parsed)

        has_next = (offset + _PAGE_SIZE) < total
        logger.info(
            "[{}] page {} (offset {}): {}/{} parsed (total={}, has_next={})",
            self.name, page, offset, len(jobs), len(raw), total, has_next,
        )
        # Enrichit avec la description complète depuis l'API CXS de chaque offre.
        # On utilise un endpoint distinct du listing : /wday/cxs/{tenant}/{site}{externalPath}
        jobs = self._enrich_workday_descriptions(jobs)
        return jobs, has_next

    def _enrich_workday_descriptions(self, jobs: list[JobBase]) -> list[JobBase]:
        """Pour chaque job, fetch l'API détail Workday et extrait jobDescription."""
        api_base = (
            f"https://{self._workday_host}/wday/cxs/"
            f"{self._workday_tenant}/{self._workday_site}"
        )
        for job in jobs:
            ext_path = job.raw_data.get("externalPath") or ""
            if not ext_path:
                continue
            detail_url = api_base + ext_path
            try:
                response = self._http_get(detail_url)
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "[{}] detail API failed for {}: {}", self.name, job.external_id, e
                )
                continue

            try:
                data = response.json()
            except ValueError:
                continue

            posting = data.get("jobPostingInfo") or {}
            html_desc = posting.get("jobDescription") or ""
            if not html_desc:
                continue
            try:
                soup = BeautifulSoup(html_desc, "lxml")
                text = soup.get_text(separator=" ", strip=True)
            except Exception:  # noqa: BLE001
                continue
            if len(text) >= 200:
                job.description = text[:8000]
        return jobs

    def _parse_posting(self, posting: dict[str, Any]) -> JobBase | None:
        title = posting.get("title")
        external_path = posting.get("externalPath") or ""
        if not title or not external_path:
            return None

        match = _PATH_RE.match(external_path)
        if match is None:
            logger.debug("[{}] cannot parse path: {}", self.name, external_path)
            return None

        job_id = match.group("job_id")
        city_raw = match.group("city")
        city_clean = city_raw.replace("-", " ").replace("%20", " ").strip()
        country = _city_to_country(city_raw)

        url = f"{self._detail_base_url}{external_path}"
        bullets = posting.get("bulletFields") or []
        posted_on = posting.get("postedOn") or ""

        # Description minimale (title + bullets + postedOn). Pour la vraie
        # description il faudrait fetch chaque page detail séparée.
        desc_parts = [title]
        if bullets:
            desc_parts.extend(str(b) for b in bullets)
        if posted_on:
            desc_parts.append(posted_on)
        description = " · ".join(desc_parts)

        return JobBase(
            source=self.source,
            external_id=job_id,
            title=title,
            company=self._company_name,
            location=city_clean,
            country=country,
            description=description,
            url=url,
            raw_data={
                "externalPath": external_path,
                "bulletFields": bullets,
                "postedOn": posted_on,
                "tenant": self._workday_tenant,
            },
        )


# ─── Factories pré-paramétrées ───────────────────────────────────────────


def build_accenture_scraper(config: SourceConfig, **kwargs: Any) -> WorkdayScraper:
    return WorkdayScraper(
        config,
        source=JobSource.ACCENTURE,
        host="accenture.wd103.myworkdayjobs.com",
        tenant="accenture",
        site="AccentureCareers",
        company_name="Accenture",
        search_text="cyber",
        **kwargs,
    )


__all__ = [
    "WorkdayScraper",
    "build_accenture_scraper",
]
