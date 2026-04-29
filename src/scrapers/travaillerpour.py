"""Scraper Travaillerpour.be — portail officiel emplois fédéraux belges.

Drupal SSR. Le listing public (filtré FR) est paginé sur ~3 pages × 18 offres.
Pattern URL d'une offre : `/fr/jobs/{type-code}{number}-{slug}`
    Exemples : `cfg26028-experts-toxicologie-humaine-mfx`,
               `xft26086-collaborateur-service-informatique-mfx`

Inclut indirectement les offres CCB, Smals (déjà scrappé séparément),
FOD/SPF Finances/Justice/Intérieur, NCCN, etc. → forte couverture du secteur
public BE.

Sprint 2 : on reste sur le listing (titre + ref-id). La page détail donnerait
plus (description complète, ministère, exigences) mais nécessite N+M requêtes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_DEFAULT_COMPANY = "Service Public Fédéral (BE)"
_DEFAULT_LOCATION = "Brussels"  # majorité des sièges fédéraux

# Capture {type-code}{number}-{slug}
# type-code (3-4 lettres) : afg, cfg, xfc, xft, ...
# Numéro : 4-6 chiffres
_HREF_RE = re.compile(
    r"^/fr/jobs/(?P<ref>[a-z]{2,5}\d{4,6})-(?P<slug>[a-z0-9-]+?)/?(?:\?.*)?$"
)


class TravaillerPourScraper(BaseScraper):
    """Scrape la liste publique paginée des emplois fédéraux belges."""

    name: ClassVar[str] = "travaillerpour"
    source: ClassVar[JobSource] = JobSource.TRAVAILLERPOUR

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        # `page` 1-indexed côté framework, 0-indexed côté URL Drupal
        url_page = page - 1
        url = f"{self.config.base_url}&page={url_page}"
        response = self._http_get(url)

        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse Travaillerpour HTML: {e}") from e

        jobs: list[JobBase] = []
        seen_refs: set[str] = set()

        for link in soup.find_all("a", href=True):
            parsed = self._parse_link(link)
            if parsed is None:
                continue
            if parsed.external_id in seen_refs:
                continue
            seen_refs.add(parsed.external_id)
            jobs.append(parsed)

        # Détection de la page suivante : on cherche un lien "page suivante"
        # ou "page N+1" dans le HTML. Si rien trouvé, has_next=False.
        next_url_marker = f"page={url_page + 1}"
        has_next = bool(jobs) and next_url_marker in response.text

        logger.info(
            "[{}] page {}: {} unique offers, has_next={}",
            self.name, page, len(jobs), has_next,
        )
        return jobs, has_next

    def _parse_link(self, link) -> JobBase | None:  # type: ignore[no-untyped-def]
        href = link.get("href", "")
        # Strip optional query string and absolute scheme prefix
        path_only = urlparse(href).path or href
        match = _HREF_RE.match(path_only)
        if match is None:
            return None

        title = link.get_text(strip=True)
        if not title or len(title) < 5:
            return None
        if title.lower() in ("retrouvez toutes les offres", "tous les jobs"):
            return None

        ref = match.group("ref")
        slug = match.group("slug")
        url = urljoin(self.config.base_url, path_only)

        return JobBase(
            source=JobSource.TRAVAILLERPOUR,
            external_id=ref,
            title=title,
            company=_DEFAULT_COMPANY,
            location=_DEFAULT_LOCATION,
            country=Country.BE,
            description=(
                f"{title}. Emploi public fédéral belge. "
                f"Référence interne : {ref}."
            ),
            url=url,
            raw_data={"ref": ref, "slug": slug, "page_url": self.config.base_url},
        )
