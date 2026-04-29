"""Tests scraper Remotive."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.remotive import (
    RemotiveScraper,
    _map_country,
    _parse_publication_date,
    _strip_html,
)
from src.storage import JobRepository


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.REST_API,
        base_url="https://remotive.com/api/remote-jobs",
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


# ─── Helpers ─────────────────────────────────────────────────────────────


def test_strip_html_simple():
    out = _strip_html("<p>Bla <b>gras</b></p>")
    assert "Bla" in out and "gras" in out
    assert "<" not in out


def test_strip_html_multiline_with_br():
    out = _strip_html("Line 1<br>Line 2<br><br>Line 3")
    assert "Line 1" in out
    assert "Line 2" in out
    assert "Line 3" in out


def test_strip_html_empty():
    assert _strip_html("") == ""
    assert _strip_html(None) == ""  # type: ignore[arg-type]


def test_strip_html_unicode_safe():
    out = _strip_html("<p>Bruxelles · cybersécurité 🔐</p>")
    assert "Bruxelles" in out
    assert "cybersécurité" in out


def test_map_country_belgium():
    country, loc = _map_country("Belgium")
    assert country == Country.BE
    assert loc == "Belgium"


def test_map_country_brussels():
    country, _ = _map_country("Brussels, Belgium")
    assert country == Country.BE


def test_map_country_luxembourg():
    country, _ = _map_country("Luxembourg")
    assert country == Country.LU


def test_map_country_worldwide_is_remote():
    country, loc = _map_country("Worldwide")
    assert country == Country.REMOTE
    assert loc == "Worldwide"


def test_map_country_empty_is_remote():
    country, loc = _map_country("")
    assert country == Country.REMOTE
    assert loc is None


def test_map_country_unknown_is_other():
    country, _ = _map_country("Mars")
    assert country == Country.OTHER


def test_parse_publication_date_iso():
    d = _parse_publication_date("2026-04-24T10:11:12")
    assert d is not None
    assert d.year == 2026


def test_parse_publication_date_with_z():
    d = _parse_publication_date("2026-04-24T10:11:12Z")
    assert d is not None


def test_parse_publication_date_invalid_returns_none():
    assert _parse_publication_date("not a date") is None
    assert _parse_publication_date("") is None
    assert _parse_publication_date(None) is None


# ─── Parsing ─────────────────────────────────────────────────────────────


SAMPLE_ITEM = {
    "id": 2089995,
    "url": "https://remotive.com/remote-jobs/security/soc-analyst",
    "title": "SOC Analyst Junior",
    "company_name": "AcmeSec",
    "category": "Software Development",
    "tags": ["cybersecurity", "junior"],
    "job_type": "full_time",
    "publication_date": "2026-04-24T10:00:00",
    "candidate_required_location": "Belgium",
    "salary": "$50k",
    "description": "<p>Junior <b>SOC</b> role.<br>Python required.</p>",
}


def test_parse_item_full_payload(cfg):
    scraper = RemotiveScraper(cfg)
    job = scraper._parse_item(SAMPLE_ITEM)
    assert job is not None
    assert job.external_id == "2089995"
    assert job.title == "SOC Analyst Junior"
    assert job.company == "AcmeSec"
    assert job.country == Country.BE
    assert "SOC" in job.description
    assert "<" not in job.description  # HTML stripped
    assert job.source == JobSource.REMOTIVE
    assert job.raw_data == SAMPLE_ITEM
    assert job.posted_at is not None


def test_parse_item_missing_id_returns_none(cfg):
    scraper = RemotiveScraper(cfg)
    bad = {**SAMPLE_ITEM}
    del bad["id"]
    assert scraper._parse_item(bad) is None


def test_parse_item_missing_company_uses_default(cfg):
    scraper = RemotiveScraper(cfg)
    bad = {**SAMPLE_ITEM}
    del bad["company_name"]
    job = scraper._parse_item(bad)
    assert job is not None
    assert job.company == "Unknown"


def test_parse_item_no_description_no_crash(cfg):
    scraper = RemotiveScraper(cfg)
    minimal = {
        "id": 1, "url": "https://example.com/j", "title": "Engineer",
    }
    job = scraper._parse_item(minimal)
    assert job is not None
    assert job.description == ""


# ─── Run intégration ─────────────────────────────────────────────────────


@respx.mock
def test_run_full_flow_persists_jobs(cfg, repo):
    respx.get("https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "00-warning": "TOS notice...",
                "job-count": 2,
                "total-job-count": 2,
                "jobs": [
                    SAMPLE_ITEM,
                    {**SAMPLE_ITEM, "id": 2089996, "title": "Another Junior"},
                ],
            },
        )
    )
    scraper = RemotiveScraper(cfg, repo=repo)
    result = scraper.run()
    assert result.jobs_fetched == 2
    assert result.jobs_inserted == 2
    assert result.aborted_reason is None


@respx.mock
def test_run_invalid_json_reports_error(cfg, repo):
    respx.get("https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(
            200, text="not json at all", headers={"content-type": "application/json"}
        )
    )
    scraper = RemotiveScraper(cfg, repo=repo)
    result = scraper.run()
    assert result.jobs_fetched == 0
    assert result.errors


@respx.mock
def test_run_empty_jobs_list_no_crash(cfg, repo):
    respx.get("https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    scraper = RemotiveScraper(cfg, repo=repo)
    result = scraper.run()
    assert result.jobs_fetched == 0
    assert result.errors == []


@respx.mock
def test_run_does_not_paginate(cfg, repo):
    """Remotive renvoie tout d'un coup → pas de page 2."""
    cfg.max_pages = 5
    route = respx.get("https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [SAMPLE_ITEM]})
    )
    scraper = RemotiveScraper(cfg, repo=repo)
    result = scraper.run()
    assert route.call_count == 1
    assert result.pages_visited == 1
