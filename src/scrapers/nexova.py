"""Scraper Nexova Group — pure-player cyber/défense BE (ESA Redu).

`careers.nexovagroup.eu/en/job-vacancies` héberge un listing HTML statique
de leurs offres ouvertes (typiquement 5-10 actives, toutes cyber). Chaque
page détail expose un bloc JSON-LD `@type=JobPosting` (avec @graph) qui
contient title, description complète, jobLocation et datePosted.

Pattern URL listing :
    /en/jobs/{date-prefix}-{slug}        ex: /en/jobs/2026-03-soc-analyst-t1
    /en/jobs/open-application            (skip — candidature spontanée)

Le scraper reste "bête" : il remonte tout, le scoring/filtre fait le tri.
Comme Nexova est un pure-player ESA-Redu (cyber spatial), toutes les offres
sont nominalement BE/cyber. La whitelist `_CYBER_PUREPLAYER_COMPANIES` du
filtre n'est PAS étendue à Nexova pour rester conservateur (les ~7 offres
contiennent toutes "cyber"/"security"/"SOC" dans le titre, donc elles
passent le gate naturellement).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import (
    BaseScraper,
    ScrapeError,
    clean_html_to_text,
    extract_city_from_jsonld_location,
    extract_jobposting_jsonld,
)


_DEFAULT_COMPANY = "Nexova Group"
_BASE_HOST = "https://www.nexovagroup.eu"
_DETAIL_FALLBACK_SELECTORS: tuple[str, ...] = (
    ".page--content",
    ".content-sidebar__content",
    "main .content",
    "article",
)

# `/en/jobs/{slug}` — exclut `/en/jobs/open-application` (candidature spontanée).
_JOB_URL_RE = re.compile(
    r"^/en/jobs/(?P<slug>(?!open-application)[a-z0-9-]+)/?$"
)
# Le link text concatène titre + métadonnées : "Title Location City, Country …"
_TITLE_FROM_LINK_RE = re.compile(r"^(.+?)\s+Location\s+", re.DOTALL)
_LOCATION_FROM_LINK_RE = re.compile(
    r"Location\s+(?P<loc>[^|]+?)(?:\s+Job\s+type|\s+Deadline|\s+Read\s+more|$)",
    re.DOTALL,
)


def _country_from_location(loc: str | None) -> Country:
    if not loc:
        return Country.BE  # défaut : Nexova HQ Redu, BE
    lc = loc.lower()
    if "luxembourg" in lc:
        return Country.LU
    if "france" in lc or "frascati" in lc:
        return Country.FR
    if "netherlands" in lc or "noordwijk" in lc:
        return Country.NL
    return Country.BE  # Belgium (Redu, Libin, Brussels…) ou autre = défaut BE


class NexovaScraper(BaseScraper):
    """Scraper Nexova Group via HTML listing + JSON-LD JobPosting."""

    name: ClassVar[str] = "nexova"
    source: ClassVar[JobSource] = JobSource.NEXOVA

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False

        response = self._http_get(self.config.base_url)
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse Nexova HTML: {e}") from e

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

        logger.info("[{}] {} unique job links found", self.name, len(jobs))
        jobs = self._enrich_with_jsonld(jobs)
        return jobs, False

    def _parse_link(self, link) -> JobBase | None:  # type: ignore[no-untyped-def]
        href = link.get("href", "")
        match = _JOB_URL_RE.match(href)
        if match is None:
            return None

        text = link.get_text(" ", strip=True)
        title_match = _TITLE_FROM_LINK_RE.match(text)
        title = (title_match.group(1).strip() if title_match else text[:80].strip())
        if not title or len(title) < 4:
            return None

        loc_match = _LOCATION_FROM_LINK_RE.search(text)
        location = loc_match.group("loc").strip() if loc_match else "Redu, Belgium"
        country = _country_from_location(location)

        slug = match.group("slug")
        url = f"{_BASE_HOST}{href}"

        return JobBase(
            source=JobSource.NEXOVA,
            external_id=slug,
            title=title,
            company=_DEFAULT_COMPANY,
            location=location,
            country=country,
            description=f"{title}. {_DEFAULT_COMPANY} — {location}.",
            url=url,
            raw_data={"href": href, "slug": slug, "listing_text": text[:500]},
        )

    def _enrich_with_jsonld(self, jobs: list[JobBase]) -> list[JobBase]:
        """Pour chaque job, fetch la page détail et extrait le JSON-LD JobPosting.

        Si pas de JSON-LD, fallback sur les sélecteurs CSS de description.
        """
        for job in jobs:
            try:
                response = self._http_get(job.url)
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "[{}] detail fetch failed for {}: {}", self.name, job.external_id, e
                )
                continue

            posting = extract_jobposting_jsonld(response.text)
            if posting is not None:
                html_desc = posting.get("description") or ""
                if html_desc:
                    text = clean_html_to_text(html_desc)
                    if len(text) >= 150:
                        job.description = text[:8000]

                org = posting.get("hiringOrganization") or {}
                if isinstance(org, dict) and org.get("name"):
                    job.company = org["name"]

                location = extract_city_from_jsonld_location(posting.get("jobLocation"))
                if location:
                    job.location = location
                    job.country = _country_from_location(location)

                if posting.get("datePosted"):
                    job.raw_data["date_posted"] = posting["datePosted"]
                if posting.get("identifier"):
                    job.raw_data["nexova_ref"] = (
                        posting["identifier"].get("value")
                        if isinstance(posting["identifier"], dict)
                        else posting["identifier"]
                    )
                continue

            # Fallback HTML — sélecteurs propres au CMS Nexova
            try:
                detail_soup = BeautifulSoup(response.text, "lxml")
            except Exception:  # noqa: BLE001
                continue
            for sel in _DETAIL_FALLBACK_SELECTORS:
                el = detail_soup.select_one(sel)
                if el is None:
                    continue
                text = clean_html_to_text(str(el))
                if len(text) >= 200:
                    job.description = text[:8000]
                    break

        return jobs
