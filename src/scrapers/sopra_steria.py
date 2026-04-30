"""Scraper Sopra Steria Belgium — Attrax (Lumesse) HTML + JSON-LD.

`careers.soprasteria.be` héberge la version belge sur la plateforme Attrax.
La homepage SSR liste ~30-60 offres BE (toutes catégories) sous la forme :

    <a href="/job/{slug}-in-{city}-belgium-jid-{id}">…</a>

Chaque page détail expose un bloc `<script type="application/ld+json">`
au schéma `JobPosting` (schema.org) → description complète gratuite.

Le scraper reste "bête" : il remonte tout, le scoring filtre cyber côté aval.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any, ClassVar

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError, clean_html_to_text


_DEFAULT_COMPANY = "Sopra Steria Belgium"
_BASE_HOST = "https://careers.soprasteria.be"

# Pattern URL Attrax : `/job/{slug}-in-{city}-belgium-jid-{id}`
# Le suffixe pays est toujours `belgium` sur le subdomain `.be`.
_JOB_URL_RE = re.compile(
    r"^/job/(?P<slug>[a-z0-9-]+?)-in-(?P<city>[a-z0-9-]+)-belgium-jid-(?P<id>\d+)/?$"
)


class SopraSteriaScraper(BaseScraper):
    """Scraper Sopra Steria Belgium via Attrax HTML + JSON-LD enrichment."""

    name: ClassVar[str] = "sopra_steria"
    source: ClassVar[JobSource] = JobSource.SOPRA_STERIA

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False

        response = self._http_get(self.config.base_url)
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse Sopra Steria HTML: {e}") from e

        jobs: list[JobBase] = []
        seen_ids: set[str] = set()

        for link in soup.find_all("a", href=True):
            parsed = self._parse_link(link)
            if parsed is None:
                continue
            if parsed.external_id in seen_ids:
                continue
            seen_ids.add(parsed.external_id)
            jobs.append(parsed)

        logger.info("[{}] {} unique BE job links found", self.name, len(jobs))
        jobs = self._enrich_with_jsonld(jobs)
        return jobs, False

    def _parse_link(self, link) -> JobBase | None:  # type: ignore[no-untyped-def]
        href = link.get("href", "")
        match = _JOB_URL_RE.match(href)
        if match is None:
            return None
        title = link.get_text(strip=True)
        if not title or len(title) < 4:
            return None

        ext_id = match.group("id")
        slug = match.group("slug")
        city_raw = match.group("city")
        city = city_raw.replace("-", " ").title()

        url = f"{_BASE_HOST}{href}"
        return JobBase(
            source=JobSource.SOPRA_STERIA,
            external_id=ext_id,
            title=title,
            company=_DEFAULT_COMPANY,
            location=city,
            country=Country.BE,
            description=f"{title}. {_DEFAULT_COMPANY} — {city}, Belgium.",
            url=url,
            raw_data={"href": href, "slug": slug, "city": city_raw},
        )

    def _enrich_with_jsonld(self, jobs: list[JobBase]) -> list[JobBase]:
        """Pour chaque job, fetch la page détail et parse le JSON-LD JobPosting."""
        for job in jobs:
            try:
                response = self._http_get(job.url)
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "[{}] detail fetch failed for {}: {}", self.name, job.external_id, e
                )
                continue

            posting = _extract_jobposting_jsonld(response.text)
            if posting is None:
                continue

            html_desc = posting.get("description") or ""
            if html_desc:
                text = clean_html_to_text(html_desc)
                if len(text) >= 200:
                    job.description = text[:8000]

            org = posting.get("hiringOrganization") or {}
            if isinstance(org, dict) and org.get("name"):
                job.company = org["name"]

            location = _extract_city(posting.get("jobLocation"))
            if location:
                job.location = location

            date_posted = posting.get("datePosted")
            if date_posted:
                job.raw_data["date_posted"] = date_posted

        return jobs


def _extract_jobposting_jsonld(html: str) -> dict[str, Any] | None:
    """Récupère le premier bloc JSON-LD `@type=JobPosting`."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _extract_city(job_location: Any) -> str | None:
    """Extrait `addressLocality` depuis jobLocation (peut être dict ou list)."""
    if not job_location:
        return None
    if isinstance(job_location, list):
        for entry in job_location:
            city = _extract_city(entry)
            if city:
                return city
        return None
    if isinstance(job_location, dict):
        address = job_location.get("address") or {}
        if isinstance(address, dict):
            city = address.get("addressLocality")
            if city and isinstance(city, str):
                return city.strip() or None
    return None
