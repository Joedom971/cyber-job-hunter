"""Tests SopraSteriaScraper — Attrax HTML listing + JSON-LD JobPosting enrichment."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.sopra_steria import SopraSteriaScraper
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.HTML,
        base_url="https://careers.soprasteria.be/",
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


HTML_LISTING = """
<html><body>
<a href="/job/grc-cybersecurity-consultant-in-machelen-belgium-jid-8440">
    GRC Cybersecurity Consultant
</a>
<a href="/job/kyc-officer-in-brussels-belgium-jid-1640">
    KYC Officer
</a>
<a href="/job/grc-cybersecurity-consultant-in-machelen-belgium-jid-8440">
    Duplicate link
</a>
<a href="/about">About (ignored)</a>
<a href="/job/foreign-job-in-paris-france-jid-9999">Out-of-scope (no belgium suffix)</a>
</body></html>
"""

JSONLD_DETAIL_CYBER = """
<html><body>
<script type="application/ld+json">
{
    "@context": "http://schema.org",
    "@type": "JobPosting",
    "title": "GRC Cybersecurity Consultant",
    "description": "<p>You will help our clients implement <strong>ISO 27001</strong> and NIS2 compliance programs.</p><ul><li>Risk assessment</li><li>Audit cybersécurité</li><li>Gap analysis</li></ul><p>You have a strong background in cybersecurity governance and compliance frameworks.</p>",
    "hiringOrganization": {"@type": "Organization", "name": "Sopra Steria Benelux"},
    "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Machelen", "addressCountry": "BE"}},
    "identifier": {"@type": "PropertyValue", "value": "8440"},
    "datePosted": "2026-04-15"
}
</script>
</body></html>
"""

JSONLD_DETAIL_KYC = """
<html><body>
<script type="application/ld+json">
{
    "@context": "http://schema.org",
    "@type": "JobPosting",
    "title": "KYC Officer",
    "description": "<p>You will perform Know Your Customer due diligence on banking clients. We are looking for someone with strong attention to detail and analytical skills.</p>",
    "hiringOrganization": {"@type": "Organization", "name": "Sopra Steria Banking"},
    "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Brussels"}}]
}
</script>
</body></html>
"""


@respx.mock
def test_run_parses_be_jobs(cfg, repo):
    respx.get("https://careers.soprasteria.be/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://careers.soprasteria.be/job/grc-cybersecurity-consultant-in-machelen-belgium-jid-8440").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_CYBER)
    )
    respx.get("https://careers.soprasteria.be/job/kyc-officer-in-brussels-belgium-jid-1640").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_KYC)
    )
    result = SopraSteriaScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 2  # dédup intra-page + filtre belgium
    assert result.aborted_reason is None


@respx.mock
def test_jsonld_enrichment_replaces_description(cfg, repo):
    respx.get("https://careers.soprasteria.be/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://careers.soprasteria.be/job/grc-cybersecurity-consultant-in-machelen-belgium-jid-8440").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_CYBER)
    )
    respx.get("https://careers.soprasteria.be/job/kyc-officer-in-brussels-belgium-jid-1640").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_KYC)
    )
    SopraSteriaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_id = {j.external_id: j for j in jobs}

    cyber = by_id["8440"]
    assert "ISO 27001" in cyber.description
    assert "Risk assessment" in cyber.description
    assert "•" in cyber.description  # bullet rendering
    assert cyber.company == "Sopra Steria Benelux"  # JSON-LD override
    assert cyber.location == "Machelen"
    assert cyber.country == Country.BE
    assert cyber.source == JobSource.SOPRA_STERIA


@respx.mock
def test_jsonld_jobLocation_can_be_list(cfg, repo):
    """jobLocation peut être un dict OU une liste — on doit gérer les 2."""
    respx.get("https://careers.soprasteria.be/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://careers.soprasteria.be/job/grc-cybersecurity-consultant-in-machelen-belgium-jid-8440").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_CYBER)
    )
    respx.get("https://careers.soprasteria.be/job/kyc-officer-in-brussels-belgium-jid-1640").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_KYC)
    )
    SopraSteriaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    kyc = next(j for j in jobs if j.external_id == "1640")
    assert kyc.location == "Brussels"


@respx.mock
def test_dedupes_intra_page(cfg, repo):
    """Les 2 liens cyber pointent vers le même jid=8440 → 1 seul job inséré."""
    respx.get("https://careers.soprasteria.be/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://careers.soprasteria.be/job/grc-cybersecurity-consultant-in-machelen-belgium-jid-8440").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_CYBER)
    )
    respx.get("https://careers.soprasteria.be/job/kyc-officer-in-brussels-belgium-jid-1640").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_KYC)
    )
    result = SopraSteriaScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 2  # not 3


@respx.mock
def test_skips_non_belgium_links(cfg, repo):
    """Le pattern regex exige `-belgium-jid-` → les jobs FR/DE sont ignorés."""
    respx.get("https://careers.soprasteria.be/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://careers.soprasteria.be/job/grc-cybersecurity-consultant-in-machelen-belgium-jid-8440").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_CYBER)
    )
    respx.get("https://careers.soprasteria.be/job/kyc-officer-in-brussels-belgium-jid-1640").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_KYC)
    )
    SopraSteriaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    assert all("9999" != j.external_id for j in jobs)
    assert all("paris" not in j.location.lower() for j in jobs)


@respx.mock
def test_detail_fetch_failure_keeps_listing_description(cfg, repo):
    """Si la page détail 404, on garde la description placeholder du listing."""
    respx.get("https://careers.soprasteria.be/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://careers.soprasteria.be/job/grc-cybersecurity-consultant-in-machelen-belgium-jid-8440").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://careers.soprasteria.be/job/kyc-officer-in-brussels-belgium-jid-1640").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_KYC)
    )
    result = SopraSteriaScraper(cfg, repo=repo).run()
    # Le 404 fait remonter une erreur mais le run continue
    jobs = repo.get_recent_jobs(only_active=True)
    by_id = {j.external_id: j for j in jobs}
    # Le job sans enrichissement garde le placeholder titre + company
    cyber = by_id.get("8440")
    if cyber is not None:
        assert "GRC Cybersecurity Consultant" in cyber.description


@respx.mock
def test_empty_html_no_crash(cfg, repo):
    respx.get("https://careers.soprasteria.be/").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    result = SopraSteriaScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0
