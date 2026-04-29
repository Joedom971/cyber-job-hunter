"""Tests RecruiteeScraper paramétrable.

NVISO a été migré sur un scraper HTML dédié (`src/scrapers/nviso.py`) après
fermeture de leur API Recruitee en avril 2026 — voir `tests/test_nviso.py`.
On teste ici via la factory `build_itsme_scraper` qui est aujourd'hui le
seul utilisateur actif de cette classe.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.recruitee import (
    RecruiteeScraper,
    _parse_recruitee_date,
    build_itsme_scraper,
)
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.RECRUITEE,
        base_url="https://itsme.recruitee.com/api/offers/",
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


SAMPLE_OFFER = {
    "id": 2584752,
    "slug": "soc-analyst-junior",
    "title": "SOC Analyst Junior",
    "city": "Brussels",
    "country": "Belgium",
    "country_code": "BE",
    "careers_url": "https://itsme.recruitee.com/o/soc-analyst-junior",
    "status": "published",
    "published_at": "2026-04-28 20:47:28 UTC",
    "created_at": "2026-04-28 20:41:46 UTC",
    "description": "<p>Looking for a <b>junior</b> SOC analyst.</p>",
    "requirements": "<ul><li>Python</li><li>Linux</li></ul>",
}


# ─── Date parsing ────────────────────────────────────────────────────────


def test_parse_recruitee_date_utc_format():
    d = _parse_recruitee_date("2026-04-28 20:47:28 UTC")
    assert d is not None
    assert d.year == 2026 and d.month == 4 and d.day == 28


def test_parse_recruitee_date_iso_fallback():
    d = _parse_recruitee_date("2026-04-28T20:47:28Z")
    assert d is not None


def test_parse_recruitee_date_invalid_returns_none():
    assert _parse_recruitee_date("nope") is None
    assert _parse_recruitee_date(None) is None


# ─── _parse_offer ────────────────────────────────────────────────────────


def test_parse_offer_full(cfg):
    scraper = build_itsme_scraper(cfg)
    job = scraper._parse_offer(SAMPLE_OFFER)
    assert job is not None
    assert job.external_id == "2584752"
    assert job.title == "SOC Analyst Junior"
    assert job.company == "itsme"
    assert job.country == Country.BE
    assert job.location == "Brussels"
    assert "junior" in job.description.lower()
    assert "Python" in job.description  # requirements concaténés
    assert job.source == JobSource.ITSME
    assert job.posted_at is not None


def test_parse_offer_unpublished_skipped(cfg):
    scraper = build_itsme_scraper(cfg)
    draft = {**SAMPLE_OFFER, "status": "draft"}
    assert scraper._parse_offer(draft) is None


def test_parse_offer_missing_id_returns_none(cfg):
    scraper = build_itsme_scraper(cfg)
    bad = {**SAMPLE_OFFER}
    del bad["id"]
    assert scraper._parse_offer(bad) is None


def test_parse_offer_unknown_country_falls_back(cfg):
    scraper = build_itsme_scraper(cfg)
    o = {**SAMPLE_OFFER, "country_code": "XX"}
    job = scraper._parse_offer(o)
    assert job is not None
    assert job.country == Country.OTHER


def test_parse_offer_no_url_uses_fallback(cfg):
    scraper = build_itsme_scraper(cfg)
    o = {**SAMPLE_OFFER}
    o["careers_url"] = ""
    job = scraper._parse_offer(o)
    assert job is not None
    assert "itsme.recruitee.com/o/soc-analyst-junior" in job.url


# ─── Factory ─────────────────────────────────────────────────────────────


def test_factory_itsme_sets_correct_source(cfg):
    scraper = build_itsme_scraper(cfg)
    assert scraper.source == JobSource.ITSME
    assert scraper._company_name == "itsme"


# ─── Run intégration ─────────────────────────────────────────────────────


@respx.mock
def test_run_full_flow_persists(cfg, repo):
    respx.get("https://itsme.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json={"offers": [SAMPLE_OFFER]})
    )
    scraper = build_itsme_scraper(cfg, repo=repo)
    result = scraper.run()
    assert result.jobs_inserted == 1
    assert result.aborted_reason is None
    assert result.source == JobSource.ITSME


@respx.mock
def test_run_empty_offers_no_crash(cfg, repo):
    respx.get("https://itsme.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json={"offers": []})
    )
    result = build_itsme_scraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0


@respx.mock
def test_run_invalid_json_reports_error(cfg, repo):
    respx.get("https://itsme.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(
            200, text="<html>error</html>",
            headers={"content-type": "application/json"},
        )
    )
    result = build_itsme_scraper(cfg, repo=repo).run()
    assert result.errors


@respx.mock
def test_run_does_not_paginate(cfg, repo):
    cfg.max_pages = 5
    route = respx.get("https://itsme.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json={"offers": [SAMPLE_OFFER]})
    )
    build_itsme_scraper(cfg, repo=repo).run()
    assert route.call_count == 1
