"""Scraper Toreon — pure-player cyber consulting BE (Antwerp HQ).

`www.toreon.com/jobs/` héberge un listing HTML statique de leurs ~7-10
offres ouvertes (toutes cyber : CISO, GRC, Cyber Risk, M365 Security).

Pattern URL listing :
    https://www.toreon.com/jobs/{slug}/
    https://www.toreon.com/jobs/spontaneous-application-2/   (skip)

Description disponible dans la page détail via le sélecteur
`.job-description` (texte propre, ~3-4K chars). Pas de JSON-LD.

Toreon est un cyber pure-player : toutes les offres sont nominalement
cyber/sécurité. Mais on n'élargit PAS la whitelist `_CYBER_PUREPLAYER_*`
du filtre — les titres contiennent toujours un mot cyber/security clair,
donc le gate `not_cyber_relevant` les laisse passer naturellement.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar

from bs4 import BeautifulSoup
from loguru import logger

from src.models import Country, JobBase, JobSource
from src.scrapers.base import BaseScraper, ScrapeError


_DEFAULT_COMPANY = "Toreon"
_DEFAULT_LOCATION = "Belgium"  # Antwerp HQ, toutes offres BE
_BASE_HOST = "https://www.toreon.com"
_DETAIL_SELECTORS: tuple[str, ...] = (
    ".job-description",
    ".entry-content",
    "main article",
    ".post-content",
)

# `https://www.toreon.com/jobs/{slug}/` — exclut spontaneous-application,
# l'index `/jobs/` lui-même, et les ancres internes.
_JOB_URL_RE = re.compile(
    r"^https://www\.toreon\.com/jobs/(?P<slug>(?!spontaneous-application)[a-z0-9-]+)/?$"
)


class ToreonScraper(BaseScraper):
    """Scraper Toreon via HTML listing + enrichissement détail."""

    name: ClassVar[str] = "toreon"
    source: ClassVar[JobSource] = JobSource.TOREON

    def fetch_jobs(self, page: int) -> tuple[Iterable[JobBase], bool]:
        if page > 1:
            return [], False

        response = self._http_get(self.config.base_url)
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as e:
            raise ScrapeError(f"Failed to parse Toreon HTML: {e}") from e

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
        jobs = self._enrich_descriptions(jobs, _DETAIL_SELECTORS)
        return jobs, False

    def _parse_link(self, link) -> JobBase | None:  # type: ignore[no-untyped-def]
        href = link.get("href", "")
        match = _JOB_URL_RE.match(href)
        if match is None:
            return None

        slug = match.group("slug")

        # Toreon utilise un pattern `.cvw-job-card` avec un <h*> pour le titre
        # et un <a class="cvw-job-read-more">Read more</a> séparé. Le link
        # text est donc "Read more" — inutile.
        # Stratégie : 1) heading DANS le link, 2) heading dans les ancêtres
        # proches, 3) link text si non générique.
        title: str | None = None

        # 1) heading enfant direct du link (cas où <a><h3>title</h3></a>)
        h_inner = link.find(["h1", "h2", "h3", "h4"])
        if h_inner and h_inner.get_text(strip=True):
            title = h_inner.get_text(strip=True)

        # 2) sinon, heading dans les ancêtres proches (.cvw-job-card pattern)
        if not title:
            parent = link
            for _ in range(4):
                parent = parent.parent
                if parent is None:
                    break
                h_el = parent.find(["h1", "h2", "h3", "h4"])
                if h_el and h_el.get_text(strip=True):
                    title = h_el.get_text(strip=True)
                    break

        # 3) sinon, link text — sauf bruit générique type "Read more"
        if not title:
            link_text = link.get_text(" ", strip=True)
            if link_text.lower() not in {"read more", "apply", "learn more",
                                          "discover", "view", "see more"}:
                title = link_text

        if not title or len(title) < 4:
            return None

        return JobBase(
            source=JobSource.TOREON,
            external_id=slug,
            title=title,
            company=_DEFAULT_COMPANY,
            location=_DEFAULT_LOCATION,
            country=Country.BE,
            description=f"{title}. {_DEFAULT_COMPANY} — {_DEFAULT_LOCATION}.",
            url=href,
            raw_data={"slug": slug, "href": href},
        )
