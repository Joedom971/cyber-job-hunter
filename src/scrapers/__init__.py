"""Registry des scrapers — mapping nom (sources.yaml) → factory.

Chaque factory accepte (config, **kwargs) et retourne un BaseScraper prêt à `run()`.
Les `kwargs` typiques sont `repo: JobRepository | None` et `client: httpx.Client | None`.
"""

from __future__ import annotations

from collections.abc import Callable

from src.config import SourceConfig
from src.scrapers.base import BaseScraper
from src.scrapers.actiris import ActirisScraper
from src.scrapers.cream import CreamScraper
from src.scrapers.easi import EasiScraper
from src.scrapers.nviso import NvisoScraper
from src.scrapers.recruitee import build_itsme_scraper
from src.scrapers.remotive import RemotiveScraper
from src.scrapers.smals import SmalsScraper
from src.scrapers.travaillerpour import TravaillerPourScraper
from src.scrapers.workday import build_accenture_scraper

ScraperFactory = Callable[..., BaseScraper]

SCRAPER_FACTORIES: dict[str, ScraperFactory] = {
    "remotive": RemotiveScraper,
    "nviso": NvisoScraper,        # HTML statique depuis avril 2026 (l'API Recruitee est morte)
    "itsme": build_itsme_scraper,
    "easi": EasiScraper,
    "smals": SmalsScraper,        # Sprint 2 — ICT sécurité sociale BE
    "cream": CreamScraper,        # Sprint 2 — ESN cyber LU (ex-Cream Consulting)
    "travaillerpour": TravaillerPourScraper,  # Sprint 2 — emplois fédéraux BE (remplace CCB)
    "actiris": ActirisScraper,                 # Sprint 2 — service public emploi Bruxelles (sitemap + détail)
    "accenture": build_accenture_scraper,      # Sprint 2 — Workday CXS API (Big4 conseil cyber)
}


def get_factory(name: str) -> ScraperFactory:
    """Retourne la factory pour ce nom de source. KeyError si inconnu."""
    return SCRAPER_FACTORIES[name]


def available_sources() -> list[str]:
    return sorted(SCRAPER_FACTORIES.keys())


def build_scraper(name: str, config: SourceConfig, **kwargs) -> BaseScraper:  # type: ignore[no-untyped-def]
    """Helper d'instanciation : `build_scraper('nviso', cfg, repo=repo)`."""
    return get_factory(name)(config, **kwargs)


__all__ = [
    "SCRAPER_FACTORIES",
    "ScraperFactory",
    "available_sources",
    "build_scraper",
    "get_factory",
]
