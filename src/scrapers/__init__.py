"""Registry des scrapers — mapping nom (sources.yaml) → factory.

Chaque factory accepte (config, **kwargs) et retourne un BaseScraper prêt à `run()`.
Les `kwargs` typiques sont `repo: JobRepository | None` et `client: httpx.Client | None`.
"""

from __future__ import annotations

from collections.abc import Callable

from src.config import SourceConfig
from src.scrapers.base import BaseScraper
from src.scrapers.easi import EasiScraper
from src.scrapers.recruitee import build_itsme_scraper, build_nviso_scraper
from src.scrapers.remotive import RemotiveScraper

ScraperFactory = Callable[..., BaseScraper]

SCRAPER_FACTORIES: dict[str, ScraperFactory] = {
    "remotive": RemotiveScraper,
    "nviso": build_nviso_scraper,
    "itsme": build_itsme_scraper,
    "easi": EasiScraper,
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
