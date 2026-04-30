"""Tests EpamScraper — Next.js _next/data API + dynamic buildId extraction."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.epam import EpamScraper
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.REST_API,
        base_url="https://careers.epam.com/en/jobs/belgium",
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


HOMEPAGE_HTML = """
<html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{},"page":"/jobs/[slug]","buildId":"abc123XYZ","isFallback":false}
</script>
</body></html>
"""

DATA_PAYLOAD = {
    "pageProps": {
        "jobs": {
            "total": 2,
            "jobs": [
                {
                    "uid": "blt5xgtx6mw8ewgwgvy",
                    "name": "Cyber Security Consultant",
                    "description": "<p>As a <strong>Cyber Security Consultant</strong> at EPAM, you will help clients address security challenges, focusing on EU Cyber Resilience Act (CRA), Supply Chain Security and ISO 27001.</p><ul><li>Risk assessment</li><li>Compliance frameworks</li></ul>",
                    "city": [{"name": "Brussels"}],
                    "country": [{"name": "Belgium"}],
                    "seniority": "Senior Management",
                    "vacancy_type": "Hybrid",
                    "skills": ["Technology Consulting"],
                    "primary_skill": "Technology Consulting",
                    "tags": ["no proofread"],
                    "tenant": "epamgdo",
                    "unique_id": "epamgdo_blt5xgtx6mw8ewgwgvy_en-us",
                    "seo": {"url": "/en/vacancy/cyber-security-consultant-blt5xgtx6mw8ewgwgvy_en"},
                },
                {
                    "uid": "bltbkyxv8l6nowp9jwd",
                    "name": "End User Support Engineer (A2)",
                    "description": "<p>We're looking for an End User Support Engineer to join our on-site team in Brussels/Ghent.</p>",
                    "city": [{"name": "Ghent"}, {"name": "Brussels"}],
                    "country": [{"name": "Belgium"}],
                    "seniority": "Middle",
                    "vacancy_type": "Office",
                    "skills": ["Support.Users"],
                    "tags": [],
                    "tenant": "epamgdo",
                    "unique_id": "epamgdo_bltbkyxv8l6nowp9jwd_en-us",
                    "seo": {"url": "/en/vacancy/end-user-support-engineer-a2-bltbkyxv8l6nowp9jwd_en"},
                },
            ],
        }
    }
}


@respx.mock
def test_run_extracts_buildid_and_fetches_jobs(cfg, repo):
    respx.get("https://careers.epam.com/en/jobs/belgium").mock(
        return_value=httpx.Response(200, text=HOMEPAGE_HTML)
    )
    respx.get(
        "https://careers.epam.com/_next/data/abc123XYZ/en/jobs/belgium.json"
    ).mock(return_value=httpx.Response(200, json=DATA_PAYLOAD))

    result = EpamScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 2
    assert result.aborted_reason is None


@respx.mock
def test_jobs_have_correct_metadata(cfg, repo):
    respx.get("https://careers.epam.com/en/jobs/belgium").mock(
        return_value=httpx.Response(200, text=HOMEPAGE_HTML)
    )
    respx.get(
        "https://careers.epam.com/_next/data/abc123XYZ/en/jobs/belgium.json"
    ).mock(return_value=httpx.Response(200, json=DATA_PAYLOAD))

    EpamScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_id = {j.external_id: j for j in jobs}

    cyber = by_id["blt5xgtx6mw8ewgwgvy"]
    assert cyber.title == "Cyber Security Consultant"
    assert cyber.country == Country.BE
    assert cyber.location == "Brussels"
    assert cyber.source == JobSource.EPAM
    assert cyber.company == "EPAM"
    assert "ISO 27001" in cyber.description
    assert "•" in cyber.description  # bullet rendering from clean_html_to_text
    assert cyber.url == "https://careers.epam.com/en/vacancy/cyber-security-consultant-blt5xgtx6mw8ewgwgvy_en"

    support = by_id["bltbkyxv8l6nowp9jwd"]
    assert support.location == "Ghent, Brussels"  # multi-cities concaténées


@respx.mock
def test_missing_buildid_raises_clear_error(cfg, repo):
    """Si __NEXT_DATA__ change, le scraper doit échouer proprement."""
    respx.get("https://careers.epam.com/en/jobs/belgium").mock(
        return_value=httpx.Response(200, text="<html><body>no script here</body></html>")
    )
    result = EpamScraper(cfg, repo=repo).run()
    assert result.errors
    assert "__NEXT_DATA__" in result.errors[0]


@respx.mock
def test_buildid_present_but_missing_field_raises(cfg, repo):
    """__NEXT_DATA__ présent mais sans champ buildId → erreur claire."""
    respx.get("https://careers.epam.com/en/jobs/belgium").mock(
        return_value=httpx.Response(
            200,
            text='<html><body><script id="__NEXT_DATA__" type="application/json">'
                 '{"props":{}}</script></body></html>',
        )
    )
    result = EpamScraper(cfg, repo=repo).run()
    assert result.errors
    assert "buildId" in result.errors[0]


@respx.mock
def test_multi_country_resolves_to_belgium_first(cfg, repo):
    """Un job avec countries = [UK, Belgium, Netherlands] doit rester en BE."""
    payload = {
        "pageProps": {"jobs": {"total": 1, "jobs": [{
            "uid": "multicountry-1",
            "name": "Cyber Security Consultant",
            "description": "<p>Pan-European role.</p>",
            "city": [],
            "country": [{"name": "UK"}, {"name": "Belgium"}, {"name": "Netherlands"}],
            "seo": {"url": "/en/vacancy/multi-1_en"},
        }]}}
    }
    respx.get("https://careers.epam.com/en/jobs/belgium").mock(
        return_value=httpx.Response(200, text=HOMEPAGE_HTML)
    )
    respx.get(
        "https://careers.epam.com/_next/data/abc123XYZ/en/jobs/belgium.json"
    ).mock(return_value=httpx.Response(200, json=payload))

    EpamScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    assert jobs[0].country == Country.BE


@respx.mock
def test_empty_jobs_list_no_crash(cfg, repo):
    respx.get("https://careers.epam.com/en/jobs/belgium").mock(
        return_value=httpx.Response(200, text=HOMEPAGE_HTML)
    )
    respx.get(
        "https://careers.epam.com/_next/data/abc123XYZ/en/jobs/belgium.json"
    ).mock(return_value=httpx.Response(200, json={"pageProps": {"jobs": {"jobs": [], "total": 0}}}))

    result = EpamScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0
    assert not result.errors
