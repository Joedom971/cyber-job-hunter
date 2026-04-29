"""Scraper Smals — ICT pour la sécurité sociale belge.

Smals expose ses offres en HTML sur `smals.be/en/jobs/list` (et /nl, /fr).
La page est en SSR Drupal — on parse directement le HTML.

Structure observée (recon 2026-04) :
    <a href="/nl/jobs/apply/7087/information-security-advisor">
        Information Security Advisor
    </a>

Tous les liens utilisent le préfixe `/nl/jobs/apply/{id}/{slug}` même sur la
version anglaise (les pages détail sont en NL/FR).

Sprint 2 : on ne fetch que la liste — pas de détail page (eviter de spammer
le rate limit). La description est minimale (titre seul) → le scoring repose
sur le titre + localisation `Brussels` (HQ Smals).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_DEFAULT_COMPANY = "Smals"
_DEFAULT_LOCATION = "Brussels"  # Siège Smals, pas exposé sur listing
_HREF_RE = re.compile(r"^/(?:[a-z]{2}/)?jobs/apply/(?P<id>\d+)/(?P<slug>[a-z0-9-]+)/?$")
_DETAIL_SELECTORS: tuple[str, ...] = (
    "div.node--type-job",          # Drupal — corps réel de l'offre Smals
    "div.node--view-mode-full",
    "div.field--name-body",
)


class SmalsScraper(BaseScraper):
    """Scrape la liste Smals via Drupal."""

    name: ClassVar[str] = "smals"
    source: ClassVar[JobSource] = JobSource.SMALS

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False

        response = self._http_get(self.config.base_url)
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse Smals HTML: {e}") from e

        seen_ids: set[str] = set()
        jobs: list[JobBase] = []
        for link in soup.find_all("a", href=True):
            parsed = self._parse_link(link)
            if parsed is None:
                continue
            if parsed.external_id in seen_ids:
                continue
            seen_ids.add(parsed.external_id)
            jobs.append(parsed)

        logger.info(
            "[{}] {} unique job links found",
            self.name, len(jobs),
        )
        jobs = self._enrich_descriptions(jobs, _DETAIL_SELECTORS)
        return jobs, False

    def _parse_link(self, link) -> JobBase | None:  # type: ignore[no-untyped-def]
        href = link.get("href", "")
        match = _HREF_RE.match(href)
        if match is None:
            return None
        title = link.get_text(strip=True)
        if not title or len(title) < 4:
            return None
        ext_id = match.group("id")
        slug = match.group("slug")

        url = urljoin(self.config.base_url, href)
        return JobBase(
            source=JobSource.SMALS,
            external_id=ext_id,
            title=title,
            company=_DEFAULT_COMPANY,
            location=_DEFAULT_LOCATION,
            country=Country.BE,
            description=f"{title}. Smals — ICT pour la sécurité sociale belge.",
            url=url,
            raw_data={"href": href, "slug": slug, "page_url": self.config.base_url},
        )
