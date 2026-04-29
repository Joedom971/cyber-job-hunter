"""Tests OrangeCyberdefenseScraper — TeamTailor HTML."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import JobSource
from src.scrapers.orange_cyberdefense import OrangeCyberdefenseScraper
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.HTML,
        base_url="https://jobs.orangecyberdefense.com/jobs",
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


HTML_FIXTURE = """
<html><body>
<a href="/jobs/3911346-consultant-cyber-security-all-genders">
    <h2>Consultant Cyber Security (all genders)</h2>
    <span>Brussels, Belgium</span>
</a>
<a href="/de/jobs/7518807-devsecops-consultant-all-genders">
    <h2>DevSecOps Consultant (all genders)</h2>
    <span>Munich, Germany</span>
</a>
<a href="/jobs/3911346-consultant-cyber-security-all-genders">
    <h3>Duplicate link should be deduped</h3>
</a>
<a href="/contact">contact (ignored)</a>
<a href="/departments/cyber">department (ignored)</a>
</body></html>
"""


@respx.mock
def test_run_parses_jobs(cfg, repo):
    respx.get("https://jobs.orangecyberdefense.com/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    result = OrangeCyberdefenseScraper(cfg, repo=repo).run()
    # 2 unique jobs (3 links but 2 IDs uniques)
    assert result.jobs_inserted == 2
    assert result.aborted_reason is None


@respx.mock
def test_run_extracts_id_and_title(cfg, repo):
    respx.get("https://jobs.orangecyberdefense.com/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    OrangeCyberdefenseScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_id = {j.external_id: j for j in jobs}

    cyber = by_id["3911346"]
    assert "Consultant Cyber Security" in cyber.title
    assert cyber.source == JobSource.ORANGE_CYBERDEFENSE
    # Non-locale URL → préfixe ajouté
    assert cyber.url.startswith("https://jobs.orangecyberdefense.com/")


@respx.mock
def test_run_dedupes_intra_page(cfg, repo):
    """Le 3e link réutilise l'ID 3911346 → dédup OK."""
    respx.get("https://jobs.orangecyberdefense.com/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    result = OrangeCyberdefenseScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 2  # not 3


@respx.mock
def test_run_empty_html_no_crash(cfg, repo):
    respx.get("https://jobs.orangecyberdefense.com/jobs").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    result = OrangeCyberdefenseScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0
    assert result.errors == []


@respx.mock
def test_run_skips_non_job_links(cfg, repo):
    """/contact et /departments ne sont PAS comptés comme jobs."""
    respx.get("https://jobs.orangecyberdefense.com/jobs").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    result = OrangeCyberdefenseScraper(cfg, repo=repo).run()
    # Verify we didn't import contact/department
    jobs = repo.get_recent_jobs(only_active=True)
    titles = {j.title for j in jobs}
    assert all("department" not in t.lower() for t in titles)
    assert all("contact" not in t.lower() for t in titles)
