"""Filtres de rejet — décident si une offre est mise hors-jeu (score = 0).

Séparé du scoring pour 2 raisons :
1. Booléen vs accumulation pondérée → testabilité
2. Filters s'exécute avant scoring → court-circuit performant

Workflow :
    >>> result = apply_filters(job, profile)
    >>> if result.is_rejected:
    ...     # score forcé à 0, raisons loggées dans rejected_by_reason
    ...     return ScoreResult(score=0, raw_score=0, is_rejected=True,
    ...                        rejection_reasons=result.reasons, ...)
    >>> # sinon → on calcule normalement
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from src.config import Profile
from src.models import JobBase, RejectReason


# ─── Résultat ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FilterResult:
    """Résultat de l'application des filtres sur une offre."""

    is_rejected: bool
    reasons: list[RejectReason] = field(default_factory=list)
    matched_patterns: dict[str, str] = field(default_factory=dict)
    """{reason_str: pattern_qui_a_matché} — pour debug et explain dashboard."""


# ─── Compilation regex (cachée par profil) ───────────────────────────────


@lru_cache(maxsize=8)
def _compile(patterns: tuple[str, ...]) -> list[re.Pattern[str]]:
    """Compile une liste de patterns une seule fois par tuple identique."""
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def _match_any(patterns: list[re.Pattern[str]], text: str) -> str | None:
    """Retourne le pattern matché (en str) ou None."""
    for p in patterns:
        if p.search(text):
            return p.pattern
    return None


# ─── Détecteurs individuels ──────────────────────────────────────────────


def detect_dutch_requirement(text: str, profile: Profile) -> tuple[bool, str | None]:
    """True si NL B2/C1/C2 est *required* sans alternative EN/FR mentionnée.

    Logique :
        1. Cherche un pattern de NL obligatoire → si aucun match : pas de rejet.
        2. Cherche un pattern d'alternative ("English or Dutch", "EN sufficient")
           → si un alternative match : pas de rejet (l'offre laisse le choix).
    """
    if not text:
        return False, None

    required_patterns = _compile(tuple(profile.dutch_required_patterns))
    matched = _match_any(required_patterns, text)
    if matched is None:
        return False, None

    # Pattern NL required matché → vérifie si une alternative désamorce
    alt_patterns = _compile(tuple(profile.language_alternative_patterns))
    if _match_any(alt_patterns, text):
        return False, None

    return True, matched


def detect_seniority(text: str, profile: Profile) -> list[tuple[RejectReason, str]]:
    """Détecte tous les patterns de seniority/expérience qui matchent.

    Sépare en 2 catégories :
        - `SENIOR_REQUIRED` : senior, lead, manager, team lead
        - `EXPERIENCE_5Y`   : 5+ years, minimum 5, etc.

    Retourne une liste car une offre peut cumuler les deux signaux
    (ex: "Lead Engineer · 5+ years experience" → 2 raisons distinctes).
    """
    if not text:
        return []

    seniority_keywords = ("senior", "lead", "manager")
    found: list[tuple[RejectReason, str]] = []
    seen_reasons: set[RejectReason] = set()

    for raw_pattern in profile.experience.reject_patterns:
        if not re.search(raw_pattern, text, re.IGNORECASE):
            continue
        reason = (
            RejectReason.SENIOR_REQUIRED
            if any(kw in raw_pattern.lower() for kw in seniority_keywords)
            else RejectReason.EXPERIENCE_5Y
        )
        if reason in seen_reasons:
            continue
        seen_reasons.add(reason)
        found.append((reason, raw_pattern))
    return found


def detect_location_out_of_scope(
    location: str | None, text: str, profile: Profile
) -> tuple[bool, str | None]:
    """True si la localisation est en Flandre ET que l'offre ne précise pas
    être en anglais uniquement.

    `location` peut venir de la source (`"Antwerp"`) ou être `None`.
    `text` est le titre+description, utilisé pour détecter "English only".
    """
    if not location:
        return False, None

    location_lower = location.lower()

    # Préférée ou Wallonie/LU → toujours OK
    for ok in profile.locations.preferred + profile.locations.good:
        if ok.lower() in location_lower:
            return False, None

    # Flandre : OK uniquement si offre 100% English
    flanders_match = next(
        (city for city in profile.locations.flanders_only_if_english
         if city.lower() in location_lower),
        None,
    )
    if flanders_match is None:
        # Localisation inconnue : on ne rejette pas (sera mappée OTHER au scoring)
        return False, None

    english_only_indicators = (
        r"\benglish-?speaking\b",
        r"\benglish[\s-]only\b",
        r"\binternational\s+(team|environment|company)\b",
        r"\bworking\s+language[: ]+english\b",
    )
    for ind in english_only_indicators:
        if re.search(ind, text, re.IGNORECASE):
            return False, None

    return True, f"flanders={flanders_match}"


# ─── Aggregator ──────────────────────────────────────────────────────────


def apply_filters(job: JobBase, profile: Profile) -> FilterResult:
    """Applique tous les filtres et agrège les raisons de rejet.

    Une offre peut accumuler plusieurs raisons (ex: senior ET dutch required) ;
    on les capture toutes pour les stats du digest.
    """
    full_text = f"{job.title}\n{job.description}"
    reasons: list[RejectReason] = []
    matched: dict[str, str] = {}

    # Seniority / 5+ years (peut cumuler les deux)
    for reason, pattern in detect_seniority(full_text, profile):
        reasons.append(reason)
        matched[reason.value] = pattern

    # Dutch required
    dutch_required, dutch_pattern = detect_dutch_requirement(full_text, profile)
    if dutch_required:
        reasons.append(RejectReason.DUTCH_REQUIRED)
        matched[RejectReason.DUTCH_REQUIRED.value] = dutch_pattern or ""

    # Localisation hors scope
    out_of_scope, loc_pattern = detect_location_out_of_scope(job.location, full_text, profile)
    if out_of_scope:
        reasons.append(RejectReason.LOCATION_OUT_OF_SCOPE)
        matched[RejectReason.LOCATION_OUT_OF_SCOPE.value] = loc_pattern or ""

    return FilterResult(
        is_rejected=bool(reasons),
        reasons=reasons,
        matched_patterns=matched,
    )
