"""Tests NexovaScraper — HTML listing + JSON-LD JobPosting (avec @graph)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.nexova import NexovaScraper
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.HTML,
        base_url="https://www.nexovagroup.eu/en/job-vacancies",
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


# ─── Fixtures HTML ───────────────────────────────────────────────────────


HTML_LISTING = """
<html><body>
<a href="/en/jobs/2026-03-soc-analyst-t1">
    SOC Analyst T1 Location Redu, Belgium Job type Permanent Deadline 15 May 2026 Read more
</a>
<a href="/en/jobs/2026-04-security-engineer-expert">
    Security Engineer Expert Location Libin, Belgium Deadline 18 May 2026 Read more
</a>
<a href="/en/jobs/2026-03-soc-analyst-t1">Duplicate link</a>
<a href="/en/jobs/open-application">Open Application (skip)</a>
<a href="/en/about">About (ignored)</a>
</body></html>
"""

JSONLD_DETAIL_SOC = """
<html><body>
<script type="application/ld+json">
{
    "@context": "https://schema.org",
    "@graph": [
        {
            "@type": "JobPosting",
            "title": "SOC Analyst T1",
            "description": "<p>Join our 24/7 SOC supporting the ESA-managed Security Operations Centre. You will <strong>monitor security events</strong>, triage alerts and escalate incidents.</p><ul><li>SIEM monitoring</li><li>Incident response</li><li>MITRE ATT&CK</li></ul>",
            "hiringOrganization": {"@type": "Organization", "name": "Nexova Group SA"},
            "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Redu", "addressCountry": "BE"}},
            "identifier": {"@type": "PropertyValue", "value": "0469"},
            "datePosted": "2026-03-23"
        }
    ]
}
</script>
</body></html>
"""

JSONLD_DETAIL_LU = """
<html><body>
<script type="application/ld+json">
{
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Security Engineer Expert",
    "description": "<p>You will design and implement security architectures across our infrastructure. Work closely with the SOC, vulnerability management, and risk teams.</p>",
    "hiringOrganization": {"@type": "Organization", "name": "Nexova Group"},
    "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Luxembourg City", "addressCountry": "LU"}}],
    "datePosted": "2026-04-10"
}
</script>
</body></html>
"""


@respx.mock
def test_run_parses_listing(cfg, repo):
    respx.get("https://www.nexovagroup.eu/en/job-vacancies").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-03-soc-analyst-t1").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_SOC)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-04-security-engineer-expert").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_LU)
    )
    result = NexovaScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 2  # dédup intra-page + skip open-application
    assert result.aborted_reason is None


@respx.mock
def test_extracts_title_and_location_from_link_text(cfg, repo):
    """Le link text concatène titre + location → titre extrait via regex."""
    respx.get("https://www.nexovagroup.eu/en/job-vacancies").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-03-soc-analyst-t1").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_SOC)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-04-security-engineer-expert").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_LU)
    )
    NexovaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_id = {j.external_id: j for j in jobs}

    soc = by_id["2026-03-soc-analyst-t1"]
    assert soc.title == "SOC Analyst T1"
    assert soc.country == Country.BE
    assert soc.source == JobSource.NEXOVA
    assert soc.company == "Nexova Group SA"  # JSON-LD override

    sec = by_id["2026-04-security-engineer-expert"]
    assert sec.title == "Security Engineer Expert"


@respx.mock
def test_jsonld_enrichment_replaces_description(cfg, repo):
    respx.get("https://www.nexovagroup.eu/en/job-vacancies").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-03-soc-analyst-t1").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_SOC)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-04-security-engineer-expert").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_LU)
    )
    NexovaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    soc = next(j for j in jobs if j.external_id == "2026-03-soc-analyst-t1")
    assert "monitor security events" in soc.description
    assert "MITRE ATT&CK" in soc.description
    assert "•" in soc.description  # bullet rendering


@respx.mock
def test_jsonld_location_override_updates_country(cfg, repo):
    """Si JSON-LD dit Luxembourg → country devient LU."""
    respx.get("https://www.nexovagroup.eu/en/job-vacancies").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-03-soc-analyst-t1").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_SOC)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-04-security-engineer-expert").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_LU)
    )
    NexovaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    sec = next(j for j in jobs if j.external_id == "2026-04-security-engineer-expert")
    assert sec.country == Country.LU
    assert sec.location == "Luxembourg City"


@respx.mock
def test_skips_open_application_link(cfg, repo):
    respx.get("https://www.nexovagroup.eu/en/job-vacancies").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-03-soc-analyst-t1").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_SOC)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-04-security-engineer-expert").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_LU)
    )
    NexovaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    assert all(j.external_id != "open-application" for j in jobs)


@respx.mock
def test_dedupes_intra_page(cfg, repo):
    respx.get("https://www.nexovagroup.eu/en/job-vacancies").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-03-soc-analyst-t1").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_SOC)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-04-security-engineer-expert").mock(
        return_value=httpx.Response(200, text=JSONLD_DETAIL_LU)
    )
    result = NexovaScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 2  # not 3 (dup link removed)


@respx.mock
def test_empty_html_no_crash(cfg, repo):
    respx.get("https://www.nexovagroup.eu/en/job-vacancies").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    result = NexovaScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0


@respx.mock
def test_fallback_html_when_no_jsonld(cfg, repo):
    """Si la page détail n'a pas de JSON-LD, le fallback CSS .page--content prend le relais."""
    minimal_listing = """<html><body>
    <a href="/en/jobs/2026-03-soc-analyst-t1">
        SOC Analyst T1 Location Redu, Belgium Deadline 1 Jan 2026 Read more
    </a>
    </body></html>"""
    fallback_detail = """<html><body><div class="page--content">
        <p>This SOC role involves monitoring SIEM tools, triaging security incidents,
        and contributing to threat hunting activities. You will work closely with
        cyber threat intelligence analysts and detection engineers in a 24/7 rotation.</p>
        <p>You bring strong analytical skills and an interest in adversary TTPs.</p>
    </div></body></html>"""
    respx.get("https://www.nexovagroup.eu/en/job-vacancies").mock(
        return_value=httpx.Response(200, text=minimal_listing)
    )
    respx.get("https://www.nexovagroup.eu/en/jobs/2026-03-soc-analyst-t1").mock(
        return_value=httpx.Response(200, text=fallback_detail)
    )
    NexovaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    soc = next(j for j in jobs if j.external_id == "2026-03-soc-analyst-t1")
    assert "SIEM tools" in soc.description
    assert "threat hunting" in soc.description
