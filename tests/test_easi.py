"""Tests EasiScraper — fixtures HTML basées sur le vrai DOM observé en recon."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.easi import (
    EasiScraper,
    _pick_primary_location,
    _slug_from_href,
    _split_locations,
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
        base_url="https://www.easi.net/en/jobs",
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


# Reproduction fidèle du DOM observé sur easi.net/en/jobs
HTML_FIXTURE = """
<html><body>
<div class="jobs-item jobs__item accordion-content cell">
    <a class="jobs-item-link" href="/en/jobs/junior-cybersecurity-consultant">
        <h3 class="jobs-item-title">Junior Cybersecurity Consultant</h3>
        <div class="jobs-item-offices">
            <div class="jobs-item-offices__location">Brussels, Nivelles, Liège</div>
        </div>
    </a>
</div>
<div class="jobs-item jobs__item accordion-content cell">
    <a class="jobs-item-link" href="/en/jobs/senior-architect">
        <h3 class="jobs-item-title">Senior Architect</h3>
        <div class="jobs-item-offices">
            <div class="jobs-item-offices__location">Antwerp</div>
        </div>
    </a>
</div>
<div class="jobs-item jobs__item accordion-content cell">
    <a class="jobs-item-link" href="/en/jobs/junior-tech-no-loc">
        <h3 class="jobs-item-title">Junior Tech Consultant</h3>
    </a>
</div>
</body></html>
"""


# ─── Helpers ─────────────────────────────────────────────────────────────


def test_slug_from_href():
    assert _slug_from_href("/en/jobs/junior-tech") == "junior-tech"
    assert _slug_from_href("/en/jobs/junior-tech/") == "junior-tech"
    assert _slug_from_href("https://www.easi.net/en/jobs/cyber") == "cyber"


def test_split_locations():
    assert _split_locations("Brussels, Liège, Luxembourg") == [
        "Brussels", "Liège", "Luxembourg",
    ]
    assert _split_locations("") == []
    assert _split_locations("Brussels") == ["Brussels"]


def test_pick_primary_brussels_first():
    assert _pick_primary_location(["Antwerp", "Brussels", "Ghent"]) == "Brussels"


def test_pick_primary_falls_back_to_good():
    assert _pick_primary_location(["Antwerp", "Liège"]) == "Liège"


def test_pick_primary_falls_back_to_first():
    assert _pick_primary_location(["Antwerp", "Ghent"]) == "Antwerp"


def test_pick_primary_empty_returns_none():
    assert _pick_primary_location([]) is None


# ─── Run intégration ─────────────────────────────────────────────────────


@respx.mock
def test_run_parses_jobs_from_html(cfg, repo):
    respx.get("https://www.easi.net/en/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    scraper = EasiScraper(cfg, repo=repo)
    result = scraper.run()
    assert result.jobs_fetched == 3
    assert result.aborted_reason is None


@respx.mock
def test_run_extracts_correct_titles_and_locations(cfg, repo):
    respx.get("https://www.easi.net/en/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    scraper = EasiScraper(cfg, repo=repo)
    scraper.run()
    # Inspecte la DB
    jobs = repo.get_recent_jobs(since_hours=24, only_active=True)
    titles = {j.title for j in jobs}
    assert "Junior Cybersecurity Consultant" in titles
    assert "Senior Architect" in titles  # rejet logique côté scoring, pas filters

    # Vérifie le mapping location preferred (Brussels picked over Antwerp/Nivelles)
    cyber_job = next(j for j in jobs if "Cybersecurity" in j.title)
    assert cyber_job.location == "Brussels"
    assert cyber_job.country == Country.BE
    assert cyber_job.source == JobSource.EASI

    # Job sans location → location=None
    no_loc = next(j for j in jobs if "Tech Consultant" in j.title)
    assert no_loc.location is None


@respx.mock
def test_run_url_built_with_urljoin(cfg, repo):
    respx.get("https://www.easi.net/en/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    scraper = EasiScraper(cfg, repo=repo)
    scraper.run()
    jobs = repo.get_recent_jobs(only_active=True)
    cyber = next(j for j in jobs if "Cybersecurity" in j.title)
    assert cyber.url.startswith("https://www.easi.net/en/jobs/")


@respx.mock
def test_run_empty_html_no_crash(cfg, repo):
    respx.get("https://www.easi.net/en/jobs").mock(
        return_value=httpx.Response(200, text="<html><body>nothing</body></html>")
    )
    result = EasiScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0
    assert result.errors == []


@respx.mock
def test_run_does_not_paginate(cfg, repo):
    cfg.max_pages = 5
    route = respx.get("https://www.easi.net/en/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    EasiScraper(cfg, repo=repo).run()
    assert route.call_count == 1


@respx.mock
def test_run_dedupes_intra_page(cfg, repo):
    """Si le HTML duplique une offre par accident, on n'insère qu'une fois."""
    duplicated = HTML_FIXTURE + HTML_FIXTURE  # 6 items mais 3 slugs uniques
    respx.get("https://www.easi.net/en/jobs").mock(
        return_value=httpx.Response(200, text=duplicated)
    )
    result = EasiScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 3
