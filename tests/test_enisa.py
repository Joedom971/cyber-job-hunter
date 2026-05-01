"""Tests EnisaScraper — listing HTML + détection pays via description."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.enisa import EnisaScraper
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.HTML,
        base_url="https://www.enisa.europa.eu/careers",
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
<a href="/recruitment/vacancies/cybersecurity-officers">Cybersecurity Officers</a>
<a href="/recruitment/vacancies/threat-and-vulnerability-analyst">Threat and Vulnerability Analyst</a>
<a href="/recruitment/vacancies/call-for-expression-of-interest-for-traineeships">Call for Expression of Interest — Traineeships</a>
<a href="/recruitment/vacancies/cybersecurity-officers">Duplicate link</a>
<a href="/topics/incident-response">Topic page (ignored)</a>
<a href="/about/who-we-are">About (ignored)</a>
</body></html>
"""

DETAIL_ATHENS = """
<html><body><main><article>
<h1>Cybersecurity Officers</h1>
<p>ENISA is seeking to draw a reserve list for the upcoming SPD period 2026-2028.
The selected candidates will work at our headquarters in <strong>Athens, Greece</strong>,
contributing to the EU cybersecurity policy framework.</p>
<p>You will support the implementation of the NIS2 Directive, conduct threat
intelligence analysis, and coordinate with national CSIRTs across Member States.
Required: Master's degree in cybersecurity or equivalent experience, working
knowledge of MITRE ATT&CK, and EU citizenship.</p>
</article></main></body></html>
"""

DETAIL_BRUSSELS = """
<html><body><main><article>
<h1>Threat and Vulnerability Analyst</h1>
<p>Position based at our <strong>Brussels</strong> office, working closely with
EU institutions on threat intelligence and vulnerability management. You will
analyze TTPs, contribute to ENISA's annual Threat Landscape report, and engage
with national CERT teams.</p>
<p>Required experience in CVSS scoring, MITRE ATT&CK, and threat hunting.</p>
</article></main></body></html>
"""

DETAIL_TRAINEESHIP = """
<html><body><main><article>
<h1>Call for Expression of Interest — Traineeships</h1>
<p>ENISA welcomes traineeship applications from recent graduates with a strong
interest in cybersecurity policy, threat intelligence, or technical operations.
Trainees are typically based at our HQ in Heraklion, Crete, with regular travel
to Athens. Six-month rotation, monthly grant.</p>
<p>Eligibility: EU national, recently graduated (within last 2 years), background
in computer science, law, or international relations with cyber focus.</p>
</article></main></body></html>
"""


@respx.mock
def test_run_parses_vacancies(cfg, repo):
    respx.get("https://www.enisa.europa.eu/careers").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/cybersecurity-officers").mock(
        return_value=httpx.Response(200, text=DETAIL_ATHENS)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/threat-and-vulnerability-analyst").mock(
        return_value=httpx.Response(200, text=DETAIL_BRUSSELS)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/call-for-expression-of-interest-for-traineeships").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    result = EnisaScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 3  # dédup
    assert result.aborted_reason is None


@respx.mock
def test_brussels_position_classified_as_BE(cfg, repo):
    respx.get("https://www.enisa.europa.eu/careers").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/cybersecurity-officers").mock(
        return_value=httpx.Response(200, text=DETAIL_ATHENS)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/threat-and-vulnerability-analyst").mock(
        return_value=httpx.Response(200, text=DETAIL_BRUSSELS)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/call-for-expression-of-interest-for-traineeships").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    EnisaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_id = {j.external_id: j for j in jobs}

    threat = by_id["threat-and-vulnerability-analyst"]
    assert threat.country == Country.BE
    assert "Brussels" in threat.location

    cyber = by_id["cybersecurity-officers"]
    assert cyber.country == Country.OTHER
    assert "Athens" in cyber.location


@respx.mock
def test_description_enrichment_via_main_article(cfg, repo):
    respx.get("https://www.enisa.europa.eu/careers").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/cybersecurity-officers").mock(
        return_value=httpx.Response(200, text=DETAIL_ATHENS)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/threat-and-vulnerability-analyst").mock(
        return_value=httpx.Response(200, text=DETAIL_BRUSSELS)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/call-for-expression-of-interest-for-traineeships").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    EnisaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    cyber = next(j for j in jobs if j.external_id == "cybersecurity-officers")
    assert "NIS2" in cyber.description
    assert "MITRE ATT&CK" in cyber.description


@respx.mock
def test_skips_non_vacancy_links(cfg, repo):
    respx.get("https://www.enisa.europa.eu/careers").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/cybersecurity-officers").mock(
        return_value=httpx.Response(200, text=DETAIL_ATHENS)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/threat-and-vulnerability-analyst").mock(
        return_value=httpx.Response(200, text=DETAIL_BRUSSELS)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/call-for-expression-of-interest-for-traineeships").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    EnisaScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    slugs = {j.external_id for j in jobs}
    assert "incident-response" not in slugs  # /topics/ pas /recruitment/vacancies/
    assert "who-we-are" not in slugs


@respx.mock
def test_dedupes_intra_page(cfg, repo):
    respx.get("https://www.enisa.europa.eu/careers").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/cybersecurity-officers").mock(
        return_value=httpx.Response(200, text=DETAIL_ATHENS)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/threat-and-vulnerability-analyst").mock(
        return_value=httpx.Response(200, text=DETAIL_BRUSSELS)
    )
    respx.get("https://www.enisa.europa.eu/recruitment/vacancies/call-for-expression-of-interest-for-traineeships").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    result = EnisaScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 3  # not 4 (dup link)


@respx.mock
def test_empty_html_no_crash(cfg, repo):
    respx.get("https://www.enisa.europa.eu/careers").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    result = EnisaScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0
