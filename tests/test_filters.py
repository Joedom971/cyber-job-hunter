"""Tests filtres de rejet — coverage cible > 80%."""

from __future__ import annotations

import pytest

from src.config import load_profile
from src.filters import (
    apply_filters,
    detect_dutch_requirement,
    detect_location_out_of_scope,
    detect_not_cyber_relevance,
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
    "title,full_text,expected",
    [
        # Niveau dans le titre → SENIOR_REQUIRED
        ("Senior SOC Analyst", "Senior SOC Analyst", RejectReason.SENIOR_REQUIRED),
        ("Lead Detection Engineer", "Lead Detection Engineer", RejectReason.SENIOR_REQUIRED),
        ("IT Manager Cybersecurity", "IT Manager Cybersecurity", RejectReason.SENIOR_REQUIRED),
        ("Team Lead Blue Team", "Team Lead Blue Team", RejectReason.SENIOR_REQUIRED),
        # Expérience dans description → EXPERIENCE_5Y (texte complet scanné)
        ("Cyber Analyst", "Looking for 5+ years experience", RejectReason.EXPERIENCE_5Y),
        ("Cyber Analyst", "Minimum 5 years in cyber required", RejectReason.EXPERIENCE_5Y),
        ("Cyber Analyst", "At least 5 years of SOC experience", RejectReason.EXPERIENCE_5Y),
        ("Cyber Analyst", "5-7 years in incident response", RejectReason.EXPERIENCE_5Y),
    ],
)
def test_detect_seniority_rejects(profile, title, full_text, expected):
    matches = detect_seniority(title, full_text, profile)
    assert len(matches) >= 1
    assert any(reason is expected for reason, _ in matches)


@pytest.mark.parametrize(
    "title,full_text",
    [
        ("Junior SOC Analyst", "Junior SOC Analyst"),
        ("Cybersecurity Trainee", "Cybersecurity Trainee"),
        ("Cyber Analyst", "0-2 years experience"),
        ("Young Graduate Program", "Young Graduate Program"),
        ("", ""),
        ("A senatorial role in IT", "A senatorial role in IT"),
    ],
)
def test_detect_seniority_passes(profile, title, full_text):
    matches = detect_seniority(title, full_text, profile)
    assert matches == []


@pytest.mark.parametrize(
    "title,full_text",
    [
        # Le mot "lead" / "manager" / "senior" / "principal" apparaît UNIQUEMENT
        # dans la description (verbe ou usage transverse) → ne doit PAS rejeter.
        # Régression : itsme "Cyber Threat Intelligence Analyst" rejetée à tort
        # parce que la description disait "people lead their digital lives".
        ("Cyber Threat Intelligence Analyst",
         "itsme has fundamentally changed how people lead their digital lives in Belgium."),
        ("SOC Analyst",
         "You will use a package manager like apt or yum."),
        ("Detection Engineer",
         "Senior management endorsement of the security strategy."),
        ("Security Consultant",
         "Working alongside the principal architects of the cloud platform."),
    ],
)
def test_detect_seniority_ignores_description_only_keywords(profile, title, full_text):
    """Patterns senior/lead/manager/principal ne s'appliquent qu'au titre."""
    matches = detect_seniority(title, full_text, profile)
    senior_matches = [m for m in matches if m[0] is RejectReason.SENIOR_REQUIRED]
    assert senior_matches == [], (
        f"Faux positif : {senior_matches} pour titre={title!r}"
    )


@pytest.mark.parametrize(
    "title",
    [
        "Junior Cyber Lead Program",
        "Cybersecurity Internship 2026",
        "Cybersecurity Trainee — SOC team lead reporting line",
        "Young Graduate Security Manager track",
        "Stagiaire Cybersécurité",
        "Alternance Cyber",
    ],
)
def test_detect_seniority_bypasses_when_title_says_junior(profile, title):
    """Titre junior/intern/trainee/graduate → ignore les patterns de niveau."""
    matches = detect_seniority(title, title, profile)
    senior_matches = [m for m in matches if m[0] is RejectReason.SENIOR_REQUIRED]
    assert senior_matches == [], (
        f"Bypass junior raté : {senior_matches} pour titre={title!r}"
    )


@pytest.mark.parametrize(
    "title,full_text",
    [
        # 5+ years reste un signal d'expérience même sur un titre junior
        ("Junior Cyber Analyst", "We require 5+ years of SOC experience."),
        ("Cybersecurity Intern", "Minimum 5 years in incident response."),
    ],
)
def test_detect_seniority_junior_bypass_does_not_skip_5years(profile, title, full_text):
    """Bypass junior n'affecte PAS les patterns d'expérience (5+ years…)."""
    matches = detect_seniority(title, full_text, profile)
    exp_matches = [m for m in matches if m[0] is RejectReason.EXPERIENCE_5Y]
    assert exp_matches, "5+ years aurait dû matcher malgré le bypass junior"


# ─── Whitelist cyber pure-players ────────────────────────────────────────


def test_cyber_pureplayer_whitelist_bypasses_relevance_gate(profile):
    """Les offres NVISO/ENISA passent le gate même sans keyword cyber dans titre/desc."""
    from src.filters import detect_not_cyber_relevance

    for company in ("NVISO", "ENISA"):
        job = _make_job(
            title="Talent Acquisition Specialist",
            description="We are looking for someone passionate about people.",
            company=company,
        )
        not_cyber, _ = detect_not_cyber_relevance(job, profile)
        assert not_cyber is False, f"{company} devait passer le gate (whitelist)"


def test_non_pureplayer_cyber_company_still_subject_to_gate(profile):
    """Cream / Orange Cyberdefense ne sont PAS dans la whitelist (branches non-cyber)."""
    from src.filters import detect_not_cyber_relevance

    for company in ("Cream by Audensiel", "Orange Cyberdefense"):
        job = _make_job(
            title="Sales Manager",
            description="We are looking for a sales manager.",
            company=company,
        )
        not_cyber, _ = detect_not_cyber_relevance(job, profile)
        assert not_cyber is True, f"{company} ne doit PAS être whitelisté"


def test_non_pureplayer_company_still_subject_to_gate(profile):
    """Une société non-whitelist sans keyword cyber est toujours rejetée."""
    from src.filters import detect_not_cyber_relevance

    job = _make_job(
        title="Talent Acquisition Specialist",
        description="We are looking for someone passionate about people.",
        company="Random ESN BE",
    )
    not_cyber, _ = detect_not_cyber_relevance(job, profile)
    assert not_cyber is True


def test_pureplayer_match_is_substring_insensitive(profile):
    """'NVISO Belgium SA' / 'ENISA HQ Athens' doivent matcher la whitelist."""
    from src.filters import detect_not_cyber_relevance

    for company in ("NVISO", "NVISO Belgium SA", "nviso", "ENISA", "ENISA HQ"):
        job = _make_job(
            title="Office Manager",
            description="Coffee and good vibes.",
            company=company,
        )
        not_cyber, _ = detect_not_cyber_relevance(job, profile)
        assert not_cyber is False, f"{company} devait matcher la whitelist"


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


# ─── Cyber relevance gate ────────────────────────────────────────────────


def test_cyber_relevance_keeps_obvious_cyber_jobs(profile):
    """Les offres avec un titre cyber évident passent le gate."""
    for title in (
        "SOC Analyst Junior",
        "Cybersecurity Consultant",
        "Junior Penetration Tester",
        "Information Security Advisor",
        "Cloud Security Engineer",
        "Threat Intelligence Junior",
        "DFIR Intern",
    ):
        job = _make_job(title=title, description="")
        not_cyber, _ = detect_not_cyber_relevance(job, profile)
        assert not_cyber is False, f"{title!r} ne devrait pas être rejeté"


def test_cyber_relevance_keeps_jobs_via_tech_keywords(profile):
    """Une offre sans titre cyber mais avec keywords tech dans la description passe."""
    job = _make_job(
        title="Junior Engineer",
        description="We use Python, SIEM, MITRE ATT&CK. Linux + Active Directory.",
    )
    not_cyber, _ = detect_not_cyber_relevance(job, profile)
    assert not_cyber is False


def test_cyber_relevance_rejects_non_cyber_jobs(profile):
    """Les offres généralistes sans aucun signal cyber sont rejetées."""
    for title, desc in (
        ("Vendeur H/F/X", "Travailler en magasin"),
        ("Educateur surveillant", "Encadrer des jeunes"),
        ("Comptable général", "Tenue de comptabilité"),
        ("Médecin Qualité médicale", "Hôpital"),
        ("Plombier", "Entretien de plomberie"),
    ):
        job = _make_job(title=title, description=desc)
        not_cyber, _ = detect_not_cyber_relevance(job, profile)
        assert not_cyber is True, f"{title!r} devrait être rejeté"


def test_cyber_relevance_word_boundary_no_false_positive(profile):
    """Le gate ne match pas 'social' à cause de 'soc', etc.

    Note : 'soc' fait 3 chars, donc filtré côté longueur. Mais on vérifie
    qu'un mot court qui ne fait PAS partie de notre vocab n'est pas rejeté
    par hasard, et qu'un mot long ne match pas un sous-mot.
    """
    job = _make_job(title="Social media manager", description="No tech here")
    # 'manager' est un reject_pattern → senior_required, mais pour le gate cyber
    # ce qui compte c'est qu'aucun keyword cyber ne soit trouvé
    not_cyber, _ = detect_not_cyber_relevance(job, profile)
    assert not_cyber is True


def test_apply_filters_rejects_non_cyber(profile):
    """Une offre 100% non-cyber est désormais rejetée par le gate."""
    job = _make_job(title="Educateur surveillant", description="")
    result = apply_filters(job, profile)
    assert result.is_rejected is True
    assert RejectReason.NOT_CYBER_RELEVANT in result.reasons


def test_apply_filters_keeps_cyber_offers(profile):
    """Une offre cyber évidente n'est PAS rejetée par le gate."""
    job = _make_job(
        title="Junior Cyber Strategy & Architecture Consultant",
        description="Junior cyber role. NIS2, ISO 27001.",
    )
    result = apply_filters(job, profile)
    assert RejectReason.NOT_CYBER_RELEVANT not in result.reasons
