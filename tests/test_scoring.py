"""Tests engine de scoring — coverage cible > 80%."""

from __future__ import annotations

import pytest

from src.config import load_profile
from src.models import Country, Job, JobSource, RejectReason
from src.scoring import (
    _score_education_penalty,
    _score_experience_3y_penalty,
    _score_graduate,
    _score_junior,
    _score_languages,
    _score_location,
    _score_target_titles,
    _score_technical_keywords,
    score_job,
)


@pytest.fixture(scope="module")
def profile():
    return load_profile()


def _make_job(
    title: str = "Job",
    description: str = "",
    location: str | None = "Brussels",
) -> Job:
    return Job(
        source=JobSource.OTHER,
        external_id="t",
        title=title,
        description=description,
        location=location,
        company="ACME",
        country=Country.BE,
        url="https://example.com/job",
        content_hash=Job.compute_content_hash(title, "ACME", location, description),
    )


# ─── Composants individuels ──────────────────────────────────────────────


def test_target_title_match(profile):
    c = _score_target_titles("SOC Analyst Junior — Brussels", profile)
    assert c is not None
    assert c.points == 30


def test_target_title_miss(profile):
    assert _score_target_titles("Backend Developer", profile) is None


def test_junior_keyword_in_description(profile):
    c = _score_junior("a junior role for trainees", profile)
    assert c is not None
    assert c.points == 15


def test_graduate_keyword_match(profile):
    c = _score_graduate("our graduate program is open", profile)
    assert c is not None
    assert c.points == 10


def test_technical_keywords_capped(profile):
    """Beaucoup de keywords matchés → bonus capé à technical_keywords_cap (30)."""
    text = (
        "we use python, bash, powershell, regex, linux, windows, docker, "
        "azure, aws, iam, wazuh, sysmon, wireshark, mitre att&ck, siem, edr, "
        "ids, ips, nmap, burp suite"
    )
    components, matched = _score_technical_keywords(text, profile)
    total = sum(c.points for c in components)
    assert total == 30
    # Au moins 6 keywords matchés (30/5)
    assert len(matched) >= 6


def test_technical_keywords_no_match(profile):
    components, matched = _score_technical_keywords("we cook pasta", profile)
    assert components == []
    assert matched == []


def test_location_preferred(profile):
    c = _score_location("Bruxelles", Country.BE, profile)
    assert c is not None
    assert c.points == 10


def test_location_good(profile):
    c = _score_location("Liège", Country.BE, profile)
    assert c is not None
    assert c.points == 5


def test_location_unknown_with_other_country(profile):
    """Country.OTHER + location inconnue → None."""
    assert _score_location("Mars", Country.OTHER, profile) is None
    assert _score_location(None, Country.OTHER, profile) is None


def test_location_fallback_country_be(profile):
    """Polish Sprint 2 : si la ville n'est pas reconnue mais country=BE → +5."""
    c = _score_location(None, Country.BE, profile)
    assert c is not None
    assert c.points == 5
    assert c.rule == "location_country_fallback"


def test_location_fallback_country_lu(profile):
    c = _score_location("Some unknown city", Country.LU, profile)
    assert c is not None
    assert c.points == 5
    assert c.rule == "location_country_fallback"


def test_location_remote_no_fallback(profile):
    """Remote / Other countries n'ont pas de fallback → None."""
    assert _score_location(None, Country.REMOTE, profile) is None


def test_languages_fr_en(profile):
    components = _score_languages("french and english required", profile)
    rules = {c.rule for c in components}
    assert "lang_fr_en" in rules


def test_languages_en_only(profile):
    components = _score_languages("english speaking team", profile)
    assert any(c.rule == "lang_en_only" for c in components)


def test_languages_fr_only(profile):
    components = _score_languages("français requis", profile)
    assert any(c.rule == "lang_fr_only" for c in components)


def test_languages_nl_nice_to_have_stacks(profile):
    """NL nice-to-have peut s'ajouter en plus du bonus FR/EN."""
    components = _score_languages(
        "english and french required. dutch is a plus.", profile
    )
    rules = [c.rule for c in components]
    assert "lang_fr_en" in rules
    assert "lang_nl_nice" in rules


def test_education_master_mandatory_penalized(profile):
    c = _score_education_penalty("Master required for this role", profile)
    assert c is not None
    assert c.points == -20


def test_education_master_with_alternative_disarms(profile):
    c = _score_education_penalty("Master or equivalent experience", profile)
    assert c is None


def test_education_bachelor_required_lighter_penalty(profile):
    c = _score_education_penalty("Bachelor degree required for this position", profile)
    assert c is not None
    assert c.points == -5


def test_experience_3y_penalty(profile):
    c = _score_experience_3y_penalty("at least 3 years in cybersecurity", profile)
    assert c is not None
    assert c.points == -10


# ─── score_job (intégration) ─────────────────────────────────────────────


def test_score_job_perfect_match_high_score(profile):
    job = _make_job(
        title="SOC Analyst Junior",
        description=(
            "Junior SOC Analyst position in our Blue Team. "
            "Tools: Python, Wazuh, Sysmon, MITRE ATT&CK. "
            "French and English required. Dutch is a plus."
        ),
        location="Brussels",
    )
    result = score_job(job, profile)
    assert result.is_rejected is False
    assert result.score >= 80
    # Breakdown contient les bonnes règles
    rules = {c["rule"] for c in result.breakdown}
    assert "target_title" in rules
    assert "junior" in rules
    assert "location_preferred" in rules
    assert "lang_fr_en" in rules
    assert "lang_nl_nice" in rules


def test_score_job_senior_rejected(profile):
    job = _make_job(
        title="Senior SOC Analyst",
        description="5+ years required",
        location="Brussels",
    )
    result = score_job(job, profile)
    assert result.is_rejected is True
    assert result.score == 0
    assert result.breakdown == []
    assert RejectReason.SENIOR_REQUIRED in result.rejection_reasons


def test_score_job_clamp_to_100(profile):
    """Une offre extrêmement matchante ne dépasse jamais 100."""
    job = _make_job(
        title="SOC Analyst Junior · Detection Engineer · Threat Intelligence",
        description=(
            "junior trainee young graduate program "
            "python bash powershell regex linux windows docker azure aws iam "
            "wazuh sysmon wireshark siem edr ids ips nmap burp suite "
            "active directory bloodhound mitre att&ck "
            "french and english required. dutch is a plus."
        ),
        location="Bruxelles",
    )
    result = score_job(job, profile)
    assert result.score == 100
    assert result.raw_score >= 100  # avant clamp


def test_score_job_clamp_to_0(profile):
    """Une offre négative cumulée → clampée à 0."""
    job = _make_job(
        title="Backend Developer",  # pas de target match
        description="Master required. 3+ years experience.",
        location="Mars",
    )
    result = score_job(job, profile)
    assert result.score == 0  # raw négatif clampé
    assert result.is_rejected is False  # pas rejeté formellement


def test_score_job_breakdown_serializable(profile):
    """Le breakdown est sérialisable JSON pour stockage SQLite."""
    import json

    job = _make_job(title="SOC Analyst Junior", description="Python, junior")
    result = score_job(job, profile)
    json.dumps(result.breakdown)  # ne doit pas crasher
    assert all(set(c.keys()) == {"rule", "points", "detail"} for c in result.breakdown)


def test_score_job_matched_keywords_unique(profile):
    """Si 'python' apparaît 3 fois dans le texte, ne compte qu'1 fois."""
    job = _make_job(
        description="python python python and bash bash linux"
    )
    result = score_job(job, profile)
    assert result.matched_keywords.count("python") == 1
    assert result.matched_keywords.count("bash") == 1


def test_score_job_realistic_nviso_style(profile):
    """Cas réaliste : offre cyber junior BE typique."""
    job = _make_job(
        title="Cybersecurity Consultant — Young Graduate",
        description=(
            "Join our team in Brussels. "
            "We are NVISO, a cybersecurity firm. "
            "0-2 years of experience. "
            "Skills: Python, Linux, Windows, MITRE ATT&CK, SIEM, threat hunting. "
            "Working language: English. Dutch is an asset."
        ),
        location="Brussels",
    )
    result = score_job(job, profile)
    assert result.score >= 60
    assert result.score <= 100


def test_score_job_anvers_with_english_passes(profile):
    """Anvers + EN-only → pas rejeté, mais pas de bonus location."""
    job = _make_job(
        title="Detection Engineer Junior",
        description="Working language: English. International team. junior.",
        location="Antwerp",
    )
    result = score_job(job, profile)
    assert result.is_rejected is False
    rules = {c["rule"] for c in result.breakdown}
    assert "location_preferred" not in rules
    assert "location_good" not in rules


def test_score_job_empty_description_no_crash(profile):
    job = _make_job(title="SOC Analyst", description="", location="Bruxelles")
    result = score_job(job, profile)
    assert result.is_rejected is False
    assert result.score >= 30  # au moins le bonus titre + location


def test_score_job_unicode_safe(profile):
    job = _make_job(
        title="Cybersécurité Junior 🔐",
        description="Stage à Bruxelles · python · français/anglais",
        location="Bruxelles",
    )
    result = score_job(job, profile)
    assert result.is_rejected is False
    assert result.score > 0
