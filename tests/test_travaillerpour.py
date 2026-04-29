"""Tests TravaillerPourScraper — Drupal HTML paginé."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.travaillerpour import TravaillerPourScraper
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.HTML,
        base_url="https://travaillerpour.be/fr/jobs?f%5B0%5D=lang%3Afr",
        rate_limit_seconds=0.0,
        jitter_max_seconds=0.0,
        max_pages=3,
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


# Fixture HTML représentative (page 1)
HTML_PAGE0 = """
<html><body>
<div class="view">
    <article>
        <a href="/fr/jobs/cfg26028-experts-toxicologie-humaine-mfx">Experts toxicologie humaine (m/f/x)</a>
        <a href="/fr/jobs/xft26086-collaborateur-service-informatique-mfx">Collaborateur service informatique (m/f/x)</a>
        <a href="/fr/jobs/afg26082-data-analyste-datawarehouse-mfx">Data analyste Datawarehouse (m/f/x)</a>
    </article>
    <a href="/fr/jobs">Retrouvez toutes les offres</a>  <!-- ignoré : titre exclu -->
    <a href="/fr/postuler">Postuler</a>  <!-- ignoré : pas /fr/jobs/<ref>- -->
</div>
<nav><a href="?f%5B0%5D=lang%3Afr&page=1">Aller à la page suivante</a></nav>
</body></html>
"""

# Page suivante (sans bouton "page suivante" → has_next=False)
HTML_PAGE1 = """
<html><body>
<a href="/fr/jobs/xfc26073-solution-architect-mfx">Solution architect (m/f/x)</a>
</body></html>
"""


# ─── Run intégration ─────────────────────────────────────────────────────


@respx.mock
def test_run_parses_first_page(cfg, repo):
    respx.get(
        "https://travaillerpour.be/fr/jobs",
        params={"f[0]": "lang:fr", "page": "0"},
    ).mock(return_value=httpx.Response(200, text=HTML_PAGE0))
    respx.get(
        "https://travaillerpour.be/fr/jobs",
        params={"f[0]": "lang:fr", "page": "1"},
    ).mock(return_value=httpx.Response(200, text=HTML_PAGE1))

    result = TravaillerPourScraper(cfg, repo=repo).run()
    assert result.aborted_reason is None
    # Page 0 : 3 jobs valides + page 1 : 1 job → 4 total
    assert result.jobs_inserted == 4
    assert result.pages_visited == 2


@respx.mock
def test_run_extracts_correct_metadata(cfg, repo):
    respx.get(
        "https://travaillerpour.be/fr/jobs",
        params={"f[0]": "lang:fr", "page": "0"},
    ).mock(return_value=httpx.Response(200, text=HTML_PAGE0))
    respx.get(
        "https://travaillerpour.be/fr/jobs",
        params={"f[0]": "lang:fr", "page": "1"},
    ).mock(return_value=httpx.Response(200, text=""))

    TravaillerPourScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_ref = {j.external_id: j for j in jobs}

    info = by_ref["xft26086"]
    assert info.title == "Collaborateur service informatique (m/f/x)"
    assert info.company == "Service Public Fédéral (BE)"
    assert info.country == Country.BE
    assert info.location == "Brussels"
    assert info.source == JobSource.TRAVAILLERPOUR
    assert info.url.startswith("https://travaillerpour.be/")


@respx.mock
def test_run_pagination_stops_when_no_next(cfg, repo):
    """Si la page courante n'a pas de marker 'page=N+1', on arrête."""
    cfg.max_pages = 5
    route0 = respx.get(
        "https://travaillerpour.be/fr/jobs",
        params={"f[0]": "lang:fr", "page": "0"},
    ).mock(return_value=httpx.Response(200, text=HTML_PAGE1))  # Sans marker page=1

    result = TravaillerPourScraper(cfg, repo=repo).run()
    assert route0.called
    assert result.pages_visited == 1
    assert result.jobs_inserted == 1


@respx.mock
def test_run_dedupes_intra_and_cross_page(cfg, repo):
    """Si un même ref-id apparaît sur plusieurs pages : 1 seule insertion."""
    respx.get(
        "https://travaillerpour.be/fr/jobs",
        params={"f[0]": "lang:fr", "page": "0"},
    ).mock(return_value=httpx.Response(200, text=HTML_PAGE0))
    # page 1 republie le 1er job de page 0
    page1_with_dup = (
        '<html><a href="/fr/jobs/cfg26028-experts-toxicologie-humaine-mfx">Already seen</a></html>'
    )
    respx.get(
        "https://travaillerpour.be/fr/jobs",
        params={"f[0]": "lang:fr", "page": "1"},
    ).mock(return_value=httpx.Response(200, text=page1_with_dup))

    result = TravaillerPourScraper(cfg, repo=repo).run()
    # 3 unique de page 0 + 0 nouveau de page 1 (cfg26028 déjà vu)
    assert result.jobs_inserted == 3


@respx.mock
def test_run_empty_html_no_crash(cfg, repo):
    respx.get(
        "https://travaillerpour.be/fr/jobs",
        params={"f[0]": "lang:fr", "page": "0"},
    ).mock(return_value=httpx.Response(200, text="<html></html>"))
    result = TravaillerPourScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0
    assert result.errors == []


@respx.mock
def test_run_skips_marketing_links(cfg, repo):
    """`Retrouvez toutes les offres` et liens utilitaires sont filtrés."""
    respx.get(
        "https://travaillerpour.be/fr/jobs",
        params={"f[0]": "lang:fr", "page": "0"},
    ).mock(return_value=httpx.Response(200, text=HTML_PAGE0))
    respx.get(
        "https://travaillerpour.be/fr/jobs",
        params={"f[0]": "lang:fr", "page": "1"},
    ).mock(return_value=httpx.Response(200, text=""))

    TravaillerPourScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    titles = {j.title for j in jobs}
    # Les 3 vraies offres présentes
    assert "Experts toxicologie humaine (m/f/x)" in titles
    assert "Collaborateur service informatique (m/f/x)" in titles
    assert "Data analyste Datawarehouse (m/f/x)" in titles
    # Le lien marketing absent
    assert "Retrouvez toutes les offres" not in titles
