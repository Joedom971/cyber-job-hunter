"""Tests NvisoScraper — HTML scraper depuis fermeture de leur Recruitee (avril 2026)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.nviso import (
    NvisoScraper,
    _extract_location_text,
    _slug_from_href,
)
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.HTML,
        base_url="https://www.nviso.eu/jobs/",
        rate_limit_seconds=0.0,
        jitter_max_seconds=0.0,
        max_pages=1,
        timeout_seconds=5.0,
        max_retries=1,
        backoff_base_seconds=0.01,
        user_agent="JobHunterBot/1.0 (+test)",
        respect_robots_txt=False,
        min_hours_between_runs=0,
    )


@pytest.fixture
def repo(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    r = JobRepository(db_url=f"sqlite:///{db_path}")
    r.create_all()
    yield r
    r.engine.dispose()


# Reproduction fidèle de la structure HTML observée sur nviso.eu/jobs/
HTML_FIXTURE = """
<html><body>
<a class="grid items-center" href="/job/junior-cyber-strategy-architecture-consultant-be">
    <h3>Junior Cyber Strategy &amp; Architecture Consultant</h3>
    <div>Belgium</div>
    <div><button>Apply now</button></div>
</a>
<a class="grid items-center" href="/job/soc-analyst-gr">
    <h3>SOC Analyst</h3>
    <div>Greece</div>
</a>
<a class="grid items-center" href="/job/junior-pentester-de">
    <h3>Junior Penetration Tester</h3>
    <div>Germany</div>
</a>
<a class="grid" href="/job/soc-analyst-online">
    <h3>SOC Analyst</h3>
    <div>Online</div>
</a>
<a class="external" href="/contact">contact</a>  <!-- non /job/ : ignoré -->
<a class="grid" href="/job/no-h3-edge-case">no h3 here</a>  <!-- pas de h3 : ignoré -->
</body></html>
"""


# ─── Helpers ─────────────────────────────────────────────────────────────


def test_slug_from_href_basic():
    assert _slug_from_href("/job/soc-analyst-gr") == "soc-analyst-gr"
    assert _slug_from_href("/job/foo/") == "foo"


# ─── Run intégration ─────────────────────────────────────────────────────


@respx.mock
def test_run_parses_jobs(cfg, repo):
    respx.get("https://www.nviso.eu/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    result = NvisoScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 4  # 4 valides, 2 ignorés (non /job/ ou sans h3)
    assert result.aborted_reason is None


@respx.mock
def test_run_extracts_correct_metadata(cfg, repo):
    respx.get("https://www.nviso.eu/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    NvisoScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_slug = {j.external_id: j for j in jobs}

    junior_be = by_slug["junior-cyber-strategy-architecture-consultant-be"]
    assert junior_be.title == "Junior Cyber Strategy & Architecture Consultant"
    assert junior_be.company == "NVISO"
    assert junior_be.location == "Belgium"
    assert junior_be.country == Country.BE
    assert junior_be.source == JobSource.NVISO
    assert junior_be.url.startswith("https://www.nviso.eu/")

    online = by_slug["soc-analyst-online"]
    assert online.country == Country.REMOTE

    greece = by_slug["soc-analyst-gr"]
    assert greece.country == Country.OTHER  # Greece pas dans notre profil
    assert greece.location == "Greece"


@respx.mock
def test_run_empty_html_no_crash(cfg, repo):
    respx.get("https://www.nviso.eu/jobs/").mock(
        return_value=httpx.Response(200, text="<html><body>no jobs here</body></html>")
    )
    result = NvisoScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0
    assert result.errors == []


@respx.mock
def test_run_does_not_paginate(cfg, repo):
    cfg.max_pages = 5
    route = respx.get("https://www.nviso.eu/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    NvisoScraper(cfg, repo=repo).run()
    assert route.call_count == 1


@respx.mock
def test_run_dedupes_intra_page(cfg, repo):
    """Si le HTML expose 2× le même slug (lien primary + lien wrap), on ne dédoublonne pas en DB."""
    duplicated = HTML_FIXTURE + HTML_FIXTURE  # tous les jobs apparaîtront 2×
    respx.get("https://www.nviso.eu/jobs/").mock(
        return_value=httpx.Response(200, text=duplicated)
    )
    result = NvisoScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 4  # uniques par slug


def test_extract_location_skips_apply_button():
    """Si le 1er div est 'Apply now', on prend le suivant (location réelle)."""
    from bs4 import BeautifulSoup

    html = """
        <a href="/job/x">
            <h3>Title</h3>
            <div><button>Apply</button></div>
            <div>Belgium</div>
        </a>
    """
    soup = BeautifulSoup(html, "lxml")
    link = soup.find("a")
    # Le 1er div contient "Apply" (un bouton) → ignoré.
    # Mais son texte global est "Apply" → notre filtre `lowered.startswith("apply")` rejette
    location = _extract_location_text(link)
    assert location == "Belgium"
