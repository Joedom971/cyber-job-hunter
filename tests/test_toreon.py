"""Tests ToreonScraper — HTML listing + description enrichment via .job-description."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.config import SourceConfig, SourceType
from src.models import Country, JobSource
from src.scrapers.toreon import ToreonScraper
from src.storage import JobRepository


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def cfg() -> SourceConfig:
    return SourceConfig(
        enabled=True,
        type=SourceType.HTML,
        base_url="https://www.toreon.com/jobs/",
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
<a href="https://www.toreon.com/jobs/traineeship-cyber-risk-information-security-officer-recent-graduates-belgium-2/">
    <h3>TRAINEESHIP Cyber Risk &amp; Information Security Officer (recent graduates – Belgium)</h3>
</a>
<a href="https://www.toreon.com/jobs/grc-security-consultant/">
    <h3>Information Security Officer</h3>
</a>
<a href="https://www.toreon.com/jobs/ciso-chief-information-security-officer-as-a-service-belgium/">
    <h3>CISO – Chief Information Security Officer (as a service – Belgium)</h3>
</a>
<a href="https://www.toreon.com/jobs/grc-security-consultant/">Duplicate link</a>
<a href="https://www.toreon.com/jobs/spontaneous-application-2/">Spontaneous (skip)</a>
<a href="https://www.toreon.com/jobs/">Index (skip)</a>
<a href="https://www.toreon.com/about">About (ignored)</a>
</body></html>
"""

DETAIL_TRAINEESHIP = """
<html><body><div class="job-description">
<h2>About us</h2>
<p>Join Toreon &amp; Data Protection Institute, where your interest in cybersecurity
and management consulting converge. At Toreon, we believe in the positive power of
technology and the value of teamwork. Our team supports clients across Belgium with
information security advisory, threat modeling, and ISO 27001 audits.</p>
<h2>Your role</h2>
<ul>
<li>Hands-on training in cybersecurity governance, risk and compliance (GRC)</li>
<li>Threat modeling workshops with senior consultants</li>
<li>ISO 27001 audit shadowing</li>
<li>NIS2 and DORA compliance projects</li>
</ul>
<h2>Your profile</h2>
<p>Recent graduate (Bachelor or Master) in IT security, computer science, or related field.
Strong analytical mindset. Curious about adversary TTPs and defensive frameworks.</p>
</div></body></html>
"""

DETAIL_CISO = """
<html><body><div class="job-description">
<p>As a CISO-as-a-Service consultant, you advise mid-sized Belgian organizations on
their cybersecurity strategy, risk posture, and compliance programs (ISO 27001, NIS2,
GDPR). Multi-client engagement with high autonomy.</p>
</div></body></html>
"""

DETAIL_GRC = """
<html><body><div class="job-description">
<p>Information Security Officer position at Toreon. You will design ISMS frameworks
following ISO 27001, conduct risk assessments, and support clients in their NIS2
compliance journey. Background in audit, GRC or security consulting required.</p>
</div></body></html>
"""


@respx.mock
def test_run_parses_jobs(cfg, repo):
    respx.get("https://www.toreon.com/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.toreon.com/jobs/traineeship-cyber-risk-information-security-officer-recent-graduates-belgium-2/").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    respx.get("https://www.toreon.com/jobs/grc-security-consultant/").mock(
        return_value=httpx.Response(200, text=DETAIL_GRC)
    )
    respx.get("https://www.toreon.com/jobs/ciso-chief-information-security-officer-as-a-service-belgium/").mock(
        return_value=httpx.Response(200, text=DETAIL_CISO)
    )
    result = ToreonScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 3  # dédup + skip spontaneous/index
    assert result.aborted_reason is None


@respx.mock
def test_extracts_title_from_nested_h3(cfg, repo):
    """Le titre est dans un <h3> à l'intérieur du <a>."""
    respx.get("https://www.toreon.com/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.toreon.com/jobs/traineeship-cyber-risk-information-security-officer-recent-graduates-belgium-2/").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    respx.get("https://www.toreon.com/jobs/grc-security-consultant/").mock(
        return_value=httpx.Response(200, text=DETAIL_GRC)
    )
    respx.get("https://www.toreon.com/jobs/ciso-chief-information-security-officer-as-a-service-belgium/").mock(
        return_value=httpx.Response(200, text=DETAIL_CISO)
    )
    ToreonScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_id = {j.external_id: j for j in jobs}

    traineeship = by_id["traineeship-cyber-risk-information-security-officer-recent-graduates-belgium-2"]
    assert "TRAINEESHIP" in traineeship.title.upper()
    assert "Cyber Risk" in traineeship.title
    assert traineeship.country == Country.BE
    assert traineeship.source == JobSource.TOREON
    assert traineeship.company == "Toreon"


@respx.mock
def test_description_enrichment_via_job_description_selector(cfg, repo):
    respx.get("https://www.toreon.com/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.toreon.com/jobs/traineeship-cyber-risk-information-security-officer-recent-graduates-belgium-2/").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    respx.get("https://www.toreon.com/jobs/grc-security-consultant/").mock(
        return_value=httpx.Response(200, text=DETAIL_GRC)
    )
    respx.get("https://www.toreon.com/jobs/ciso-chief-information-security-officer-as-a-service-belgium/").mock(
        return_value=httpx.Response(200, text=DETAIL_CISO)
    )
    ToreonScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    by_id = {j.external_id: j for j in jobs}

    traineeship = by_id["traineeship-cyber-risk-information-security-officer-recent-graduates-belgium-2"]
    assert "ISO 27001" in traineeship.description
    assert "NIS2" in traineeship.description
    assert "•" in traineeship.description  # bullets converted


@respx.mock
def test_skips_spontaneous_and_index(cfg, repo):
    respx.get("https://www.toreon.com/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.toreon.com/jobs/traineeship-cyber-risk-information-security-officer-recent-graduates-belgium-2/").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    respx.get("https://www.toreon.com/jobs/grc-security-consultant/").mock(
        return_value=httpx.Response(200, text=DETAIL_GRC)
    )
    respx.get("https://www.toreon.com/jobs/ciso-chief-information-security-officer-as-a-service-belgium/").mock(
        return_value=httpx.Response(200, text=DETAIL_CISO)
    )
    ToreonScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    slugs = {j.external_id for j in jobs}
    assert "spontaneous-application-2" not in slugs
    assert "" not in slugs


@respx.mock
def test_dedupes_intra_page(cfg, repo):
    respx.get("https://www.toreon.com/jobs/").mock(
        return_value=httpx.Response(200, text=HTML_LISTING)
    )
    respx.get("https://www.toreon.com/jobs/traineeship-cyber-risk-information-security-officer-recent-graduates-belgium-2/").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    respx.get("https://www.toreon.com/jobs/grc-security-consultant/").mock(
        return_value=httpx.Response(200, text=DETAIL_GRC)
    )
    respx.get("https://www.toreon.com/jobs/ciso-chief-information-security-officer-as-a-service-belgium/").mock(
        return_value=httpx.Response(200, text=DETAIL_CISO)
    )
    result = ToreonScraper(cfg, repo=repo).run()
    assert result.jobs_inserted == 3  # not 4 (le 2e link grc-security-consultant est un dup)


@respx.mock
def test_empty_html_no_crash(cfg, repo):
    respx.get("https://www.toreon.com/jobs/").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    result = ToreonScraper(cfg, repo=repo).run()
    assert result.jobs_fetched == 0


@respx.mock
def test_real_toreon_card_pattern_extracts_title(cfg, repo):
    """Régression : la vraie structure Toreon est <div class="cvw-job-card">
    <h3>TITRE</h3><a class="cvw-job-read-more">Read more</a></div>.
    Le link text est 'Read more' qu'on doit ignorer pour récupérer le h3."""
    real_pattern = """<html><body>
    <div class="cvw-job-card">
        <h3>TRAINEESHIP Cyber Risk &amp; Information Security Officer</h3>
        <a class="cvw-job-read-more"
           href="https://www.toreon.com/jobs/traineeship-cyber-risk-information-security-officer-recent-graduates-belgium-2/">
            Read more
        </a>
    </div>
    </body></html>"""
    respx.get("https://www.toreon.com/jobs/").mock(
        return_value=httpx.Response(200, text=real_pattern)
    )
    respx.get("https://www.toreon.com/jobs/traineeship-cyber-risk-information-security-officer-recent-graduates-belgium-2/").mock(
        return_value=httpx.Response(200, text=DETAIL_TRAINEESHIP)
    )
    ToreonScraper(cfg, repo=repo).run()
    jobs = repo.get_recent_jobs(only_active=True)
    assert len(jobs) == 1
    assert "TRAINEESHIP" in jobs[0].title.upper()
    assert "Read more" not in jobs[0].title  # bruit ne doit PAS être le titre
