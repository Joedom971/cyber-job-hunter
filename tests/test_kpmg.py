"""Tests KpmgScraper — RSS TalentSoft Belgium."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.kpmg import KpmgScraper, _clean_title, _strip_html
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.RSS,
        base_url="https://kpmg-career.talent-soft.com/handlers/offerRss.ashx?LCID=2057",
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


RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>RSS export of vacancies</title>
    <link>https://kpmg-career.talent-soft.com/handlers/offerRss.ashx?LCID=2057</link>
    <item>
      <link>https://kpmg-career.talent-soft.com/Pages/Offre/detailoffre.aspx?idOffre=1394&amp;idOrigine=502&amp;LCID=2057</link>
      <category>Advisory/Senior Advisor</category>
      <category>Permanent</category>
      <category>Luchthaven Brussel Nationaal 1K 1930 Zaventem</category>
      <title>2026-1394 - Cloud Security Specialist</title>
      <description>&lt;b&gt;Function : &lt;/b&gt;Advisory&lt;br /&gt;Working on cybersecurity projects with KPMG team.</description>
      <pubDate>Wed, 29 Apr 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <link>https://kpmg-career.talent-soft.com/Pages/Offre/detailoffre.aspx?idOffre=1210&amp;idOrigine=502&amp;LCID=2057</link>
      <category>Tax and Legal/Tax Adviser</category>
      <category>Permanent</category>
      <title>2025-1210 - Junior Tax Consultant</title>
      <description>Tax consultant role.</description>
      <pubDate>Tue, 28 Apr 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <link>https://invalid-link-no-id.example.com/foo</link>
      <title>Bad entry should be skipped</title>
      <description>no idOffre param</description>
    </item>
  </channel>
</rss>
"""


# ─── Helpers ─────────────────────────────────────────────────────────────


def test_clean_title_strips_year_id():
    assert _clean_title("2026-1394 - Cloud Security Specialist") == "Cloud Security Specialist"
    assert _clean_title("2025-1210 - Junior Tax Consultant") == "Junior Tax Consultant"
    assert _clean_title("Plain title") == "Plain title"


def test_strip_html_with_br():
    out = _strip_html("Line 1<br>Line 2<br><br>Line 3")
    assert "Line 1" in out
    assert "Line 2" in out


def test_strip_html_empty():
    assert _strip_html("") == ""
    assert _strip_html(None) == ""  # type: ignore[arg-type]


# ─── Run intégration ─────────────────────────────────────────────────────


@respx.mock
def test_run_parses_feed(cfg, repo):
    respx.get("https://kpmg-career.talent-soft.com/handlers/offerRss.ashx").mock(
        return_value=httpx.Response(200, text=RSS_FIXTURE,
                                    headers={"content-type": "text/xml; charset=utf-8"})
    )
    result = KpmgScraper(cfg, repo=repo).run()
    assert result.aborted_reason is None
    # 2 valid entries (3rd has no idOffre param → skipped)
    assert result.jobs_inserted == 2


@respx.mock
def test_run_extracts_correct_metadata(cfg, repo):
    respx.get("https://kpmg-career.talent-soft.com/handlers/offerRss.ashx").mock(
        return_value=httpx.Response(200, text=RSS_FIXTURE)
    )
    KpmgScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_id = {j.external_id: j for j in jobs}

    cyber = by_id["1394"]
    assert cyber.title == "Cloud Security Specialist"
    assert cyber.company == "KPMG Belgium"
    assert cyber.country == Country.BE
    assert cyber.location == "Zaventem"
    assert cyber.source == JobSource.KPMG
    assert "cybersecurity" in cyber.description.lower()
    assert cyber.url.startswith("https://kpmg-career.talent-soft.com/")
    assert cyber.posted_at is not None


@respx.mock
def test_run_skips_invalid_entries(cfg, repo):
    """Une entry sans idOffre dans le link est skippée."""
    respx.get("https://kpmg-career.talent-soft.com/handlers/offerRss.ashx").mock(
        return_value=httpx.Response(200, text=RSS_FIXTURE)
    )
    result = KpmgScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 2  # 3 entries dans le feed, 1 invalide


@respx.mock
def test_run_does_not_paginate(cfg, repo):
    cfg.max_pages = 5
    route = respx.get("https://kpmg-career.talent-soft.com/handlers/offerRss.ashx").mock(
        return_value=httpx.Response(200, text=RSS_FIXTURE)
    )
    KpmgScraper(cfg, repo=repo).run()
    assert route.call_count == 1
