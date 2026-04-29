"""Tests WorkdayScraper paramétrable (Accenture comme cas standard)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.workday import (
    WorkdayScraper,
    _city_to_country,
    build_accenture_scraper,
)
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.WORKDAY,
        base_url=(
            "https://accenture.wd103.myworkdayjobs.com/wday/cxs/"
            "accenture/AccentureCareers/jobs"
        ),
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


def _page_response(jobs: list[dict], total: int = 100) -> dict:
    return {"total": total, "jobPostings": jobs, "facets": []}


SAMPLE_JOB_BE = {
    "title": "Young Graduate - Cybersecurity - As of September 2026",
    "externalPath": "/job/Brussels/Young-Graduate---Cybersecurity_R00324817",
    "postedOn": "Posted Today",
    "bulletFields": ["R00324817", "Brussels"],
}
SAMPLE_JOB_DE = {
    "title": "Cyber Security Analyst",
    "externalPath": "/job/Berlin/Cyber-Security-Analyst_R00286677",
    "postedOn": "Posted 5 days ago",
    "bulletFields": ["R00286677", "Berlin"],
}
SAMPLE_JOB_OTHER = {
    "title": "Cyber Security Academy",
    "externalPath": "/job/Assago/Cyber-Security-Academy_R00326010",
    "postedOn": "Posted Today",
    "bulletFields": ["R00326010", "Location Negotiable"],
}


# ─── Helper ──────────────────────────────────────────────────────────────


def test_city_to_country():
    assert _city_to_country("Brussels") == Country.BE
    assert _city_to_country("Bruxelles") == Country.BE
    assert _city_to_country("Antwerp") == Country.BE
    assert _city_to_country("Luxembourg") == Country.LU
    assert _city_to_country("Paris") == Country.FR
    assert _city_to_country("Dublin") == Country.IE
    assert _city_to_country("Berlin") == Country.DE
    assert _city_to_country("Buenos-Aires") == Country.OTHER  # pas dans la liste
    assert _city_to_country("UnknownCity") == Country.OTHER


# ─── Run intégration ─────────────────────────────────────────────────────


@respx.mock
def test_run_parses_first_page(cfg, repo):
    respx.post(
        "https://accenture.wd103.myworkdayjobs.com/wday/cxs/accenture/AccentureCareers/jobs"
    ).mock(
        return_value=httpx.Response(
            200, json=_page_response([SAMPLE_JOB_BE, SAMPLE_JOB_DE], total=2)
        )
    )
    cfg.max_pages = 1
    result = build_accenture_scraper(cfg, repo=repo).run()
    assert result.aborted_reason is None
    assert result.jobs_inserted == 2


@respx.mock
def test_run_extracts_correct_metadata(cfg, repo):
    respx.post(
        "https://accenture.wd103.myworkdayjobs.com/wday/cxs/accenture/AccentureCareers/jobs"
    ).mock(
        return_value=httpx.Response(200, json=_page_response([SAMPLE_JOB_BE], total=1))
    )
    cfg.max_pages = 1
    build_accenture_scraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.external_id == "R00324817"
    assert j.title == "Young Graduate - Cybersecurity - As of September 2026"
    assert j.company == "Accenture"
    assert j.country == Country.BE
    assert j.location == "Brussels"
    assert j.source == JobSource.ACCENTURE
    assert "wd103.myworkdayjobs.com" in j.url
    assert "Posted Today" in j.description


@respx.mock
def test_run_paginates_via_offset(cfg, repo):
    """Page 1 → offset=0, page 2 → offset=20. has_next=False quand offset+limit >= total."""
    page1 = _page_response([{**SAMPLE_JOB_BE, "externalPath": f"/job/Brussels/J{i}_R{i:05d}"} for i in range(20)], total=25)
    page2 = _page_response([{**SAMPLE_JOB_DE, "externalPath": f"/job/Berlin/J{i}_R{i:05d}"} for i in range(20, 25)], total=25)
    route = respx.post(
        "https://accenture.wd103.myworkdayjobs.com/wday/cxs/accenture/AccentureCareers/jobs"
    ).mock(side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])

    cfg.max_pages = 5
    result = build_accenture_scraper(cfg, repo=repo).run()
    assert route.call_count == 2
    assert result.jobs_inserted == 25


@respx.mock
def test_run_skips_invalid_paths(cfg, repo):
    """Un externalPath qui ne match pas le pattern Workday est ignoré."""
    bad = {"title": "T", "externalPath": "/weird/path/no-id-here", "bulletFields": []}
    respx.post(
        "https://accenture.wd103.myworkdayjobs.com/wday/cxs/accenture/AccentureCareers/jobs"
    ).mock(return_value=httpx.Response(200, json=_page_response([SAMPLE_JOB_BE, bad], total=2)))
    cfg.max_pages = 1
    result = build_accenture_scraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 1  # SAMPLE_JOB_BE OK, bad ignoré


@respx.mock
def test_run_invalid_json_reports_error(cfg, repo):
    respx.post(
        "https://accenture.wd103.myworkdayjobs.com/wday/cxs/accenture/AccentureCareers/jobs"
    ).mock(
        return_value=httpx.Response(
            200, text="not json", headers={"content-type": "application/json"}
        )
    )
    result = build_accenture_scraper(cfg, repo=repo).run()
    assert result.errors


@respx.mock
def test_run_unknown_city_falls_back_to_other(cfg, repo):
    respx.post(
        "https://accenture.wd103.myworkdayjobs.com/wday/cxs/accenture/AccentureCareers/jobs"
    ).mock(
        return_value=httpx.Response(200, json=_page_response([SAMPLE_JOB_OTHER], total=1))
    )
    cfg.max_pages = 1
    build_accenture_scraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    assert jobs[0].country == Country.OTHER
    assert jobs[0].location == "Assago"


def test_factory_accenture_sets_correct_source(cfg):
    s = build_accenture_scraper(cfg)
    assert s.source == JobSource.ACCENTURE
    assert s._workday_tenant == "accenture"
    assert s._workday_site == "AccentureCareers"
    assert s._company_name == "Accenture"
