"""Tests SmalsScraper — HTML Drupal."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.smals import SmalsScraper
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.HTML,
        base_url="https://www.smals.be/en/jobs/list",
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


# Fixture HTML basée sur la structure observée en recon
HTML_FIXTURE = """
<html><body>
<a href="/nl/jobs/apply/7087/information-security-advisor">Information Security Advisor</a>
<a href="/nl/jobs/apply/7153/security-architect">Security Architect</a>
<a href="/fr/jobs/apply/7036/it-support-officer">IT Support Officer</a>
<a href="/jobs/apply/4689/it-project-manager">IT Project Manager</a>
<a href="/nl/jobs/apply/7087/information-security-advisor">Information Security Advisor</a>  <!-- doublon -->
<a href="/nl/jobs/list">Alle vacatures</a>  <!-- ignoré : pas /apply/ -->
<a href="/contact">Contact</a>  <!-- ignoré : autre pattern -->
<a href="https://external.test/job/123">External</a>  <!-- ignoré : autre host -->
</body></html>
"""


# ─── Run intégration ─────────────────────────────────────────────────────


@respx.mock
def test_run_parses_jobs(cfg, repo):
    respx.get("https://www.smals.be/en/jobs/list").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    result = SmalsScraper(cfg, repo=repo).run()
    # 4 unique IDs (7087, 7153, 7036, 4689) — doublon dédupliqué intra-page
    assert result.jobs_inserted == 4
    assert result.aborted_reason is None


@respx.mock
def test_run_extracts_correct_metadata(cfg, repo):
    respx.get("https://www.smals.be/en/jobs/list").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    SmalsScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_ext = {j.external_id: j for j in jobs}

    sec_advisor = by_ext["7087"]
    assert sec_advisor.title == "Information Security Advisor"
    assert sec_advisor.company == "Smals"
    assert sec_advisor.location == "Brussels"
    assert sec_advisor.country == Country.BE
    assert sec_advisor.source == JobSource.SMALS
    assert sec_advisor.url.startswith("https://www.smals.be/")


@respx.mock
def test_run_ignores_non_job_links(cfg, repo):
    """Les liens vers /jobs/list, /contact, etc. ne sont pas comptés."""
    respx.get("https://www.smals.be/en/jobs/list").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    result = SmalsScraper(cfg, repo=repo).run()
    # Si /jobs/list ou /contact étaient pris, on aurait > 4
    assert result.jobs_inserted == 4


@respx.mock
def test_run_empty_html_no_crash(cfg, repo):
    respx.get("https://www.smals.be/en/jobs/list").mock(
        return_value=httpx.Response(200, text="<html><body>nothing</body></html>")
    )
    result = SmalsScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0
    assert result.errors == []


@respx.mock
def test_run_skips_short_titles(cfg, repo):
    """Liens valides mais avec un titre vide / trop court → ignorés."""
    respx.get("https://www.smals.be/en/jobs/list").mock(
        return_value=httpx.Response(
            200,
            text='<a href="/nl/jobs/apply/9999/blah">A</a>',  # 1 caractère
        )
    )
    result = SmalsScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 0


@respx.mock
def test_run_does_not_paginate(cfg, repo):
    cfg.max_pages = 5
    route = respx.get("https://www.smals.be/en/jobs/list").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    SmalsScraper(cfg, repo=repo).run()
    assert route.call_count == 1
