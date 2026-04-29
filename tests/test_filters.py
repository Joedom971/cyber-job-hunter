"""Tests filtres de rejet — coverage cible > 80%."""

from __future__ import annotations

import pytest

from src.config import load_profile
from src.filters import (
    apply_filters,
    detect_dutch_requirement,
    detect_location_out_of_scope,
    detect_seniority,
)
from src.models import Country, Job, JobBase, JobSource, RejectReason


@pytest.fixture(scope="module")
def profile():
    return load_profile()


def _make_job(
    title: str = "Job",
    description: str = "",
    location: str | None = "Brussels",
    company: str = "ACME",
) -> JobBase:
    return Job(
        source=JobSource.OTHER,
        external_id="test",
        title=title,
        description=description,
        location=location,
        company=company,
        country=Country.BE,
        url="https://example.com/job",
        content_hash=Job.compute_content_hash(title, company, location, description),
    )


# ─── detect_seniority ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Senior SOC Analyst", RejectReason.SENIOR_REQUIRED),
        ("Lead Detection Engineer", RejectReason.SENIOR_REQUIRED),
        ("IT Manager Cybersecurity", RejectReason.SENIOR_REQUIRED),
        ("Team Lead Blue Team", RejectReason.SENIOR_REQUIRED),
        ("Looking for 5+ years experience", RejectReason.EXPERIENCE_5Y),
        ("Minimum 5 years in cyber required", RejectReason.EXPERIENCE_5Y),
        ("At least 5 years of SOC experience", RejectReason.EXPERIENCE_5Y),
        ("5-7 years in incident response", RejectReason.EXPERIENCE_5Y),
    ],
)
def test_detect_seniority_rejects(profile, text, expected):
    matches = detect_seniority(text, profile)
    assert len(matches) >= 1
    assert any(reason is expected for reason, _ in matches)


@pytest.mark.parametrize(
    "text",
    [
        "Junior SOC Analyst",
        "Cybersecurity Trainee",
        "0-2 years experience",
        "Young Graduate Program",
        "",
        "A senatorial role in IT",  # 'senior' contenu dans 'senatorial' → \bsenior\b ne match pas
    ],
)
def test_detect_seniority_passes(profile, text):
    matches = detect_seniority(text, profile)
    assert matches == []


# ─── detect_dutch_requirement ────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,should_reject",
    [
        ("Dutch is mandatory for this role", True),
        ("Dutch C1 required", True),
        ("Nederlands C2 vereist", True),
        ("Native Dutch speaker", True),
        ("Néerlandais courant exigé", True),
        ("Fluent in Dutch is required", True),
    ],
)
def test_detect_dutch_required_rejects(profile, text, should_reject):
    is_rejected, _ = detect_dutch_requirement(text, profile)
    assert is_rejected is should_reject


@pytest.mark.parametrize(
    "text",
    [
        "Dutch is a plus",
        "Dutch nice to have",
        "Knowledge of Dutch is an asset",
        "English or Dutch required",
        "Dutch C1 required, OR English is sufficient",
        "Working in an English-speaking team",
        "",
    ],
)
def test_detect_dutch_passes(profile, text):
    is_rejected, _ = detect_dutch_requirement(text, profile)
    assert is_rejected is False


def test_detect_dutch_with_alternative_disarms(profile):
    """Si l'offre dit 'Dutch C1 required' MAIS aussi 'English or Dutch', pas de rejet."""
    text = "Native Dutch speaker preferred. However, English or Dutch is fine."
    is_rejected, _ = detect_dutch_requirement(text, profile)
    assert is_rejected is False


# ─── detect_location_out_of_scope ────────────────────────────────────────


@pytest.mark.parametrize(
    "location,description",
    [
        ("Brussels", ""),
        ("Bruxelles", ""),
        ("Etterbeek", ""),
        ("Liège", ""),
        ("Luxembourg City", ""),
        ("Brabant wallon", ""),
        (None, ""),  # location inconnue → pas de rejet (laisse passer)
        ("Remote", ""),
    ],
)
def test_location_in_scope(profile, location, description):
    out, _ = detect_location_out_of_scope(location, description, profile)
    assert out is False


@pytest.mark.parametrize(
    "location",
    ["Antwerp", "Anvers", "Gent", "Ghent", "Leuven"],
)
def test_location_flanders_rejected(profile, location):
    out, _ = detect_location_out_of_scope(location, "Beautiful office", profile)
    assert out is True


@pytest.mark.parametrize(
    "description",
    [
        "We work in an English-speaking team",
        "International environment",
        "Working language: English",
        "English-only company culture",
    ],
)
def test_location_flanders_ok_if_english_only(profile, description):
    out, _ = detect_location_out_of_scope("Antwerp", description, profile)
    assert out is False


# ─── apply_filters (intégration) ─────────────────────────────────────────


def test_apply_filters_clean_job_passes(profile):
    job = _make_job(
        title="SOC Analyst Junior",
        description="Junior role in our Blue Team. We use Python, Wazuh, Sysmon.",
        location="Brussels",
    )
    result = apply_filters(job, profile)
    assert result.is_rejected is False
    assert result.reasons == []


def test_apply_filters_senior_rejected(profile):
    job = _make_job(
        title="Senior SOC Analyst",
        description="5+ years in cyber required",
        location="Brussels",
    )
    result = apply_filters(job, profile)
    assert result.is_rejected is True
    # Au moins une des deux raisons attendues
    assert any(r in result.reasons for r in (
        RejectReason.SENIOR_REQUIRED, RejectReason.EXPERIENCE_5Y
    ))


def test_apply_filters_multiple_reasons_accumulated(profile):
    """Une offre peut cumuler plusieurs raisons → toutes capturées."""
    job = _make_job(
        title="Lead Engineer",
        description="Native Dutch speaker required. 5+ years experience.",
        location="Antwerp",
    )
    result = apply_filters(job, profile)
    assert result.is_rejected is True
    # Senior + Dutch + Flanders → 3 raisons
    assert RejectReason.SENIOR_REQUIRED in result.reasons
    assert RejectReason.DUTCH_REQUIRED in result.reasons
    assert RejectReason.LOCATION_OUT_OF_SCOPE in result.reasons


def test_apply_filters_dutch_no_alternative_rejected(profile):
    job = _make_job(
        title="Cybersecurity Analyst",
        description="Dutch C1 mandatory. We're looking for natives.",
        location="Brussels",
    )
    result = apply_filters(job, profile)
    assert RejectReason.DUTCH_REQUIRED in result.reasons


def test_apply_filters_dutch_with_english_alternative_passes(profile):
    job = _make_job(
        title="Cybersecurity Analyst",
        description="Dutch C1 required. English or Dutch is acceptable.",
        location="Brussels",
    )
    result = apply_filters(job, profile)
    assert RejectReason.DUTCH_REQUIRED not in result.reasons


def test_apply_filters_flanders_with_english_passes(profile):
    job = _make_job(
        title="Detection Engineer",
        description="Working language: English. International team.",
        location="Antwerp",
    )
    result = apply_filters(job, profile)
    assert result.is_rejected is False


def test_apply_filters_unicode_safe(profile):
    """Caractères spéciaux / emojis ne crashent pas."""
    job = _make_job(
        title="Cybersécurité Junior 🔐",
        description="Stage à Bruxelles, équipe DFIR — n'attend qu'un·e candidat·e !",
        location="Bruxelles",
    )
    result = apply_filters(job, profile)
    assert result.is_rejected is False


def test_apply_filters_empty_description(profile):
    job = _make_job(title="SOC Analyst", description="", location="Brussels")
    result = apply_filters(job, profile)
    assert result.is_rejected is False


def test_filter_result_matched_patterns_populated(profile):
    """Le mapping reason → pattern matché est utilisable pour debug."""
    job = _make_job(title="Senior Analyst", description="", location="Brussels")
    result = apply_filters(job, profile)
    assert "senior_required" in result.matched_patterns
    assert "senior" in result.matched_patterns["senior_required"].lower()
