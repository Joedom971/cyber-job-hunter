"""Tests CapgeminiScraper — API custom Azure."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.capgemini import CapgeminiScraper
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.REST_API,
        base_url="https://cg-jobstream-api.azurewebsites.net/api/job-search",
        rate_limit_seconds=0.0,
        jitter_max_seconds=0.0,
        max_pages=2,
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


SAMPLE_JOB = {
    "id": "abc123",
    "ref": "JOB-12345",
    "title": "Cybersecurity Operations Specialist SIAM",
    "description_stripped": "We are looking for a security specialist...",
    "country_code": "be-en",
    "country_name": "Belgium",
    "location": "Diegem",
    "brand": "Capgemini",
    "experience_level": "Junior",
    "education_level": "Bachelor",
    "contract_type": "Permanent",
}


@respx.mock
def test_run_parses_capgemini_jobs(cfg, repo):
    respx.get("https://cg-jobstream-api.azurewebsites.net/api/job-search").mock(
        return_value=httpx.Response(
            200,
            json={"data": [SAMPLE_JOB], "count": 1, "total": 1},
        )
    )
    cfg.max_pages = 1
    result = CapgeminiScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 1
    assert result.aborted_reason is None


@respx.mock
def test_run_extracts_metadata(cfg, repo):
    respx.get("https://cg-jobstream-api.azurewebsites.net/api/job-search").mock(
        return_value=httpx.Response(
            200, json={"data": [SAMPLE_JOB], "total": 1, "count": 1}
        )
    )
    CapgeminiScraper(cfg, repo=repo).run()
    [job] = repo.get_recent_jobs(only_active=True)
    assert job.external_id == "abc123"
    assert job.title == "Cybersecurity Operations Specialist SIAM"
    assert job.country == Country.BE
    assert job.location == "Diegem"
    assert job.source == JobSource.CAPGEMINI


@respx.mock
def test_run_paginates(cfg, repo):
    page1 = {
        "data": [{**SAMPLE_JOB, "id": str(i)} for i in range(20)],
        "total": 25, "count": 20,
    }
    page2 = {
        "data": [{**SAMPLE_JOB, "id": f"p2-{i}"} for i in range(5)],
        "total": 25, "count": 5,
    }
    route = respx.get("https://cg-jobstream-api.azurewebsites.net/api/job-search").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    cfg.max_pages = 5
    result = CapgeminiScraper(cfg, repo=repo).run()
    assert route.call_count == 2
    assert result.jobs_inserted == 25


@respx.mock
def test_run_skips_invalid_items(cfg, repo):
    bad = {"description_stripped": "no id no title"}
    respx.get("https://cg-jobstream-api.azurewebsites.net/api/job-search").mock(
        return_value=httpx.Response(
            200, json={"data": [SAMPLE_JOB, bad], "total": 2, "count": 2}
        )
    )
    cfg.max_pages = 1
    result = CapgeminiScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 1


@respx.mock
def test_run_invalid_json_reports_error(cfg, repo):
    respx.get("https://cg-jobstream-api.azurewebsites.net/api/job-search").mock(
        return_value=httpx.Response(
            200, text="not json", headers={"content-type": "application/json"}
        )
    )
    result = CapgeminiScraper(cfg, repo=repo).run()
    assert result.errors


@respx.mock
def test_run_uses_belgium_country_filter(cfg, repo):
    """Vérifie que le param country_code=be-en est bien envoyé."""
    route = respx.get("https://cg-jobstream-api.azurewebsites.net/api/job-search").mock(
        return_value=httpx.Response(200, json={"data": [], "total": 0, "count": 0})
    )
    cfg.max_pages = 1
    CapgeminiScraper(cfg, repo=repo).run()
    assert route.called
    last_request = route.calls.last.request
    assert "country_code=be-en" in str(last_request.url)
    assert "search=cyber" in str(last_request.url)
