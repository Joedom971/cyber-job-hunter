"""Tests DevoteamScraper — Google Cloud Talent Solution API."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.devoteam import DevoteamScraper, _country_from_address, _strip_html
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.REST_API,
        base_url="https://europe-west1-dsi-careers.cloudfunctions.net/careers-api/v1.1",
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


SAMPLE_JOB_BE = {
    "job": {
        "name": "projects/dsi-careers/tenants/x/jobs/abc123",
        "title": "Digital Identity Cybersecurity Consultant",
        "addresses": ["Culliganlaan 3 Machelen Belgium"],
        "description": "<p>Help clients design IAM solutions.</p>",
        "qualifications": "<p>Bachelor in IT.</p>",
        "responsibilities": "<p>Implement <b>IAM</b> projects.</p>",
        "applicationInfo": {"uris": ["https://www.devoteam.com/jobs/digital-identity-123"]},
    },
    "jobSummary": "IAM consulting summary",
}

SAMPLE_JOB_LU = {
    "job": {
        "name": "projects/dsi-careers/tenants/x/jobs/lu999",
        "title": "Cyber Security Engineer",
        "addresses": ["Avenue de la Liberté Luxembourg"],
        "description": "<p>Security role LU.</p>",
        "applicationInfo": {"uris": ["https://www.devoteam.com/jobs/cyber-lu-999"]},
    },
}


# ─── Helpers ─────────────────────────────────────────────────────────────


def test_country_from_address_belgium():
    country, city = _country_from_address(["Culliganlaan 3 Machelen Belgium"])
    assert country == Country.BE
    assert city is not None


def test_country_from_address_luxembourg():
    country, _ = _country_from_address(["Avenue de la Liberté Luxembourg"])
    assert country == Country.LU


def test_country_from_address_empty():
    country, city = _country_from_address([])
    assert country == Country.OTHER
    assert city is None


def test_strip_html():
    assert _strip_html("<p>Hello <b>World</b></p>") == "Hello World"
    assert _strip_html("") == ""


# ─── Run intégration ─────────────────────────────────────────────────────


@respx.mock
def test_run_parses_jobs(cfg, repo):
    respx.get(
        "https://europe-west1-dsi-careers.cloudfunctions.net/careers-api/v1.1"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "totalSize": 2,
                "matchingJobs": [SAMPLE_JOB_BE, SAMPLE_JOB_LU],
            },
        )
    )
    cfg.max_pages = 1
    result = DevoteamScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 2
    assert result.aborted_reason is None


@respx.mock
def test_run_extracts_metadata(cfg, repo):
    respx.get(
        "https://europe-west1-dsi-careers.cloudfunctions.net/careers-api/v1.1"
    ).mock(
        return_value=httpx.Response(
            200, json={"totalSize": 1, "matchingJobs": [SAMPLE_JOB_BE]}
        )
    )
    cfg.max_pages = 1
    DevoteamScraper(cfg, repo=repo).run()
    [job] = repo.get_recent_jobs(only_active=True)
    assert job.external_id == "abc123"
    assert job.title == "Digital Identity Cybersecurity Consultant"
    assert job.country == Country.BE
    assert job.source == JobSource.DEVOTEAM
    assert "IAM" in job.description
    assert job.url.startswith("https://www.devoteam.com/")


@respx.mock
def test_run_paginates_via_offset(cfg, repo):
    page1 = {
        "totalSize": 25,
        "matchingJobs": [
            {**SAMPLE_JOB_BE, "job": {**SAMPLE_JOB_BE["job"],
                                       "name": f"projects/x/jobs/p1-{i}"}}
            for i in range(15)
        ],
    }
    page2 = {
        "totalSize": 25,
        "matchingJobs": [
            {**SAMPLE_JOB_BE, "job": {**SAMPLE_JOB_BE["job"],
                                       "name": f"projects/x/jobs/p2-{i}"}}
            for i in range(10)
        ],
    }
    route = respx.get(
        "https://europe-west1-dsi-careers.cloudfunctions.net/careers-api/v1.1"
    ).mock(side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])

    cfg.max_pages = 5
    result = DevoteamScraper(cfg, repo=repo).run()
    assert route.call_count == 2
    assert result.jobs_inserted == 25


@respx.mock
def test_run_invalid_json_reports_error(cfg, repo):
    respx.get(
        "https://europe-west1-dsi-careers.cloudfunctions.net/careers-api/v1.1"
    ).mock(
        return_value=httpx.Response(
            200, text="not json", headers={"content-type": "application/json"}
        )
    )
    result = DevoteamScraper(cfg, repo=repo).run()
    assert result.errors


@respx.mock
def test_run_uses_belgium_filter(cfg, repo):
    """Vérifie que country=Belgium est bien envoyé."""
    route = respx.get(
        "https://europe-west1-dsi-careers.cloudfunctions.net/careers-api/v1.1"
    ).mock(return_value=httpx.Response(200, json={"totalSize": 0, "matchingJobs": []}))
    cfg.max_pages = 1
    DevoteamScraper(cfg, repo=repo).run()
    assert route.called
    last = route.calls.last.request
    assert "country=Belgium" in str(last.url)
    assert "Authorization" in last.headers
