"""Registry des scrapers — mapping nom (sources.yaml) → factory.

Chaque factory accepte (config, **kwargs) et retourne un BaseScraper prêt à `run()`.
Les `kwargs` typiques sont `repo: JobRepository | None` et `client: httpx.Client | None`.
"""

from __future__ import annotations

from collections.abc import Callable

from src.config import SourceConfig
from src.scrapers.base import BaseScraper
from src.scrapers.actiris import ActirisScraper
from src.scrapers.capgemini import CapgeminiScraper
from src.scrapers.cream import CreamScraper
from src.scrapers.devoteam import DevoteamScraper
from src.scrapers.easi import EasiScraper
from src.scrapers.enisa import EnisaScraper
from src.scrapers.epam import EpamScraper
from src.scrapers.kpmg import KpmgScraper
from src.scrapers.nexova import NexovaScraper
from src.scrapers.orange_cyberdefense import OrangeCyberdefenseScraper
from src.scrapers.nviso import NvisoScraper
from src.scrapers.recruitee import build_itsme_scraper
from src.scrapers.remotive import RemotiveScraper
from src.scrapers.smals import SmalsScraper
from src.scrapers.sopra_steria import SopraSteriaScraper
from src.scrapers.toreon import ToreonScraper
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
    "kpmg": KpmgScraper,                        # Sprint 2+ — RSS feed TalentSoft Belgium
    "capgemini": CapgeminiScraper,              # Sprint 2+ — API custom Azure (BE filtered)
    "orange_cyberdefense": OrangeCyberdefenseScraper,  # Sprint 2+ — TeamTailor HTML
    "devoteam": DevoteamScraper,                # Sprint 2+ — HTML listing multi-pays (FR/BE/LU)
    "sopra_steria": SopraSteriaScraper,         # Sprint 3 — Attrax HTML + JSON-LD JobPosting
    "nexova": NexovaScraper,                    # Sprint 3 — pure-player cyber/défense BE (ESA Redu)
    "epam": EpamScraper,                        # Sprint 3 — Next.js _next/data API (BE filtered)
    "toreon": ToreonScraper,                    # Sprint 3 — pure-player cyber consulting BE (Antwerp)
    "enisa": EnisaScraper,                      # Sprint 3 — agence cyber UE (Athens HQ + Brussels office)
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
