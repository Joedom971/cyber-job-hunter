"""Tests DevoteamScraper — HTML listing only (les pages détail bloquent notre UA)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import JobSource
from src.scrapers.devoteam import DevoteamScraper, _country_from_text
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.HTML,
        base_url="https://www.devoteam.com/jobs/",
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
<a href="https://www.devoteam.com/jobs/cloud-security-engineer-100812534256149190">
    <h2>Cloud Security Engineer Brussels, Permanent contract</h2>
</a>
<a href="https://www.devoteam.com/jobs/banking-sector-devops-engineer-133756331428324038">
    <h2>Banking Sector DevOps Engineer Luxembourg</h2>
</a>
<a href="https://www.devoteam.com/jobs/cloud-security-engineer-100812534256149190">
    <h3>duplicate, will be deduped</h3>
</a>
<a href="/contact">contact (ignored)</a>
<a href="https://www.devoteam.com/about">about (ignored)</a>
</body></html>
"""


def test_country_detection():
    assert _country_from_text("Brussels Belgium") == _country_from_text("brussels")
    assert _country_from_text("Working in Luxembourg City") == _country_from_text("luxembourg")
    assert _country_from_text("Paris office") == _country_from_text("paris")


@respx.mock
def test_run_parses_jobs(cfg, repo):
    respx.get("https://www.devoteam.com/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    result = DevoteamScraper(cfg, repo=repo).run()
    assert result.aborted_reason is None
    # 2 unique jobs (3rd link is dup, contact/about ignored)
    assert result.jobs_inserted == 2


@respx.mock
def test_run_extracts_id_and_title(cfg, repo):
    respx.get("https://www.devoteam.com/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    DevoteamScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_id = {j.external_id: j for j in jobs}

    cloud = by_id["100812534256149190"]
    assert "Cloud Security Engineer" in cloud.title
    # ", Permanent contract" stripped from title
    assert "Permanent" not in cloud.title
    assert cloud.source == JobSource.DEVOTEAM


@respx.mock
def test_run_dedupes_intra_page(cfg, repo):
    respx.get("https://www.devoteam.com/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_FIXTURE)
    )
    result = DevoteamScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 2  # not 3


@respx.mock
def test_run_empty_html_no_crash(cfg, repo):
    respx.get("https://www.devoteam.com/jobs/").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    result = DevoteamScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0
