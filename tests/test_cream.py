"""Tests CreamScraper — Cream by Audensiel HTML."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.cream import CreamScraper, _is_job_title
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.HTML,
        base_url="https://www.creamconsulting.com/jobs",
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


# Reproduit la structure observée : <h2> + lien /project/<slug>/ dans le parent
HTML_FIXTURE = """
<html><body>
<div>
    <h2>WE WANT YOU</h2>  <!-- pas un poste, ignoré -->
</div>
<div class="card">
    <h2>CYBERSECURITY ANALYST</h2>
    <p>Contract type</p>
    <a href="https://creamconsulting.com/project/cybersecurity-analyst/">Apply</a>
</div>
<div class="card">
    <h2>Python Software Engineer</h2>
    <p>Contract type</p>
    <a href="https://creamconsulting.com/project/python-software-engineer/">Apply</a>
</div>
<div class="card">
    <h2>DEVOPS ENGINEER</h2>
    <a href="https://creamconsulting.com/project/devops-engineer/">Read more</a>
</div>
<div class="card">
    <h2>BAD CARD</h2>  <!-- pas un job (heuristique) → ignoré -->
    <a href="https://creamconsulting.com/project/some-slug/">Apply</a>
</div>
<div class="card">
    <h2>Java Developer</h2>
    <!-- pas de lien /project/ → ignoré -->
</div>
</body></html>
"""


# ─── Helper _is_job_title ────────────────────────────────────────────────


def test_is_job_title_keywords():
    assert _is_job_title("Python Software Engineer") is True
    assert _is_job_title("CYBERSECURITY ANALYST") is True
    assert _is_job_title("Lead DevOps Architect") is True
    assert _is_job_title(".NET DEVELOPPER") is True  # typo Cream tolérée


def test_is_job_title_rejects_marketing():
    assert _is_job_title("WE WANT YOU") is False
    assert _is_job_title("OUR APPROACH") is False
    assert _is_job_title("") is False
    assert _is_job_title("X" * 101) is False  # trop long


# ─── Run intégration ─────────────────────────────────────────────────────


@respx.mock
def test_run_parses_jobs(cfg, repo):
    respx.get("https://www.creamconsulting.com/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    result = CreamScraper(cfg, repo=repo).run()
    # 3 jobs valides : CYBERSECURITY ANALYST, Python SE, DEVOPS Engineer
    # WE WANT YOU et BAD CARD filtrés (mauvais h2), Java Dev sans lien
    assert result.jobs_inserted == 3
    assert result.aborted_reason is None


@respx.mock
def test_run_extracts_correct_metadata(cfg, repo):
    respx.get("https://www.creamconsulting.com/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    CreamScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_slug = {j.external_id: j for j in jobs}

    cyber = by_slug["cybersecurity-analyst"]
    assert cyber.title == "CYBERSECURITY ANALYST"
    assert cyber.company == "Cream by Audensiel"
    assert cyber.country == Country.LU  # Luxembourg
    assert cyber.location == "Luxembourg"
    assert cyber.source == JobSource.CREAM
    assert cyber.url == "https://creamconsulting.com/project/cybersecurity-analyst/"


@respx.mock
def test_run_dedupes_by_slug(cfg, repo):
    """Si la même offre apparaît 2× dans le HTML (même slug), on n'insère qu'1 fois."""
    duplicated = HTML_FIXTURE + HTML_FIXTURE
    respx.get("https://www.creamconsulting.com/jobs").mock(
        return_value=httpx.Response(200, text=duplicated)
    )
    result = CreamScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 3  # même nombre que sans doublon


@respx.mock
def test_run_empty_html_no_crash(cfg, repo):
    respx.get("https://www.creamconsulting.com/jobs").mock(
        return_value=httpx.Response(200, text="<html><body></body></html>")
    )
    result = CreamScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0
    assert result.errors == []


@respx.mock
def test_run_does_not_paginate(cfg, repo):
    cfg.max_pages = 5
    route = respx.get("https://www.creamconsulting.com/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    CreamScraper(cfg, repo=repo).run()
    assert route.call_count == 1
