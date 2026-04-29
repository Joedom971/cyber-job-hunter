"""Engine de scoring — accumule des ScoreComponent et clamp le total dans [0, 100].

Workflow :
    >>> result = score_job(job, profile)
    >>> if result.is_rejected:
    ...     # filters a coupé court → score = 0, breakdown vide
    ...     ...
    >>> print(result.score, result.matched_keywords, result.breakdown)

Chaque règle est une fonction privée `_score_*` qui retourne un `ScoreComponent`
(ou None si pas de match). Cette structure permet d'expliquer chaque point dans
le dashboard ("pourquoi cette offre a 72/100 ?") sans logique cachée.
"""

from __future__ import annotations

import re
from typing import Any

from src.config import Profile
from src.filters import apply_filters
from src.models import JobBase, ScoreComponent, ScoreResult


# ─── Helpers de matching ─────────────────────────────────────────────────


def _contains_any(text: str, needles: list[str]) -> str | None:
    """Retourne le 1er needle (lowercase) trouvé dans text (lowercase) ou None."""
    for n in needles:
        if n.lower() in text:
            return n
    return None


def _regex_search(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return p
    return None


# ─── Règles de scoring (chacune retourne un ScoreComponent ou None) ──────


def _score_target_titles(title: str, profile: Profile) -> ScoreComponent | None:
    """+30 si un titre cible apparaît dans le titre de l'offre."""
    matched = _contains_any(title.lower(), profile.target_titles)
    if matched is None:
        return None
    return ScoreComponent(rule="target_title", points=30, detail=matched)


def _score_junior(text_lower: str, profile: Profile) -> ScoreComponent | None:
    """+15 si un mot-clé de séniorité junior apparaît dans titre/description."""
    matched = _contains_any(text_lower, profile.junior_keywords)
    if matched is None:
        return None
    return ScoreComponent(rule="junior", points=15, detail=matched)


def _score_graduate(text_lower: str, profile: Profile) -> ScoreComponent | None:
    """+10 si 'young graduate' / 'graduate program' apparaît."""
    matched = _contains_any(text_lower, profile.graduate_keywords)
    if matched is None:
        return None
    return ScoreComponent(rule="graduate", points=10, detail=matched)


def _score_technical_keywords(
    text_lower: str, profile: Profile
) -> tuple[list[ScoreComponent], list[str]]:
    """+5 par mot-clé technique matché, capé à `technical_keywords_cap`.

    Retourne aussi la liste plate des keywords matchés (pour `ScoreResult.matched_keywords`).
    """
    cap = profile.score_bounds.technical_keywords_cap
    components: list[ScoreComponent] = []
    matched: list[str] = []
    total = 0

    for kw in profile.technical_keywords.all_flat:
        if total >= cap:
            break
        if kw.lower() in text_lower and kw not in matched:
            matched.append(kw)
            points = min(5, cap - total)
            total += points
            components.append(
                ScoreComponent(rule="tech_keyword", points=points, detail=kw)
            )
    return components, matched


def _score_location(location: str | None, profile: Profile) -> ScoreComponent | None:
    """+10 préférée / +5 wallonie+LU. Flanders accepté avec EN-only → 0 bonus."""
    if not location:
        return None
    loc_lower = location.lower()

    if _contains_any(loc_lower, profile.locations.preferred) is not None:
        return ScoreComponent(
            rule="location_preferred", points=10, detail=location
        )
    if _contains_any(loc_lower, profile.locations.good) is not None:
        return ScoreComponent(
            rule="location_good", points=5, detail=location
        )
    return None


def _score_languages(text_lower: str, profile: Profile) -> list[ScoreComponent]:
    """Bonus langue. Au plus un parmi FR+EN / EN seul / FR seul, **+ NL nice-to-have**.

    Ordre de priorité (du plus fort au plus faible) :
        - FR + EN  → french_english_bonus
        - EN seul  → english_only_bonus (mention claire EN dans l'offre)
        - FR seul  → french_only_bonus
    Et indépendamment :
        - NL "nice to have / plus / atout" → dutch_nice_to_have_bonus (cumulable)
    """
    components: list[ScoreComponent] = []

    fr_en_patterns = (
        r"\b(french|fr)\s*(and|&|\+|/|et)\s*(english|en|anglais)\b",
        r"\b(english|en|anglais)\s*(and|&|\+|/|et)\s*(french|fr|fran[cç]ais)\b",
        r"\bfr/en\b|\ben/fr\b",
    )
    en_only_patterns = (
        r"\benglish\s+(speaking|only|required|mandatory|fluent)\b",
        r"\bworking\s+language[: ]+english\b",
        r"\benglish-?speaking\b",
    )
    fr_only_patterns = (
        r"\bfrench\s+(speaking|only|required|mandatory|fluent)\b",
        r"\bfran[cç]ais\s+(courant|requis|obligatoire)\b",
    )
    nl_nice_patterns = (
        r"\bdutch\s+(is\s+)?(a\s+)?(plus|asset|nice\s+to\s+have|bonus)\b",
        r"\bnederlands\s+(is\s+)?(een\s+)?(plus|pluspunt|bonus)\b",
        r"\bdutch\s+is\s+an\s+asset\b",
        r"\bn[eé]erlandais\s+(est\s+)?(un\s+)?(atout|plus|bonus)\b",
    )

    if _regex_search(list(fr_en_patterns), text_lower):
        components.append(
            ScoreComponent(
                rule="lang_fr_en",
                points=profile.languages.french_english_bonus,
                detail="FR + EN",
            )
        )
    elif _regex_search(list(en_only_patterns), text_lower):
        components.append(
            ScoreComponent(
                rule="lang_en_only",
                points=profile.languages.english_only_bonus,
                detail="English",
            )
        )
    elif _regex_search(list(fr_only_patterns), text_lower):
        components.append(
            ScoreComponent(
                rule="lang_fr_only",
                points=profile.languages.french_only_bonus,
                detail="French",
            )
        )

    if _regex_search(list(nl_nice_patterns), text_lower):
        components.append(
            ScoreComponent(
                rule="lang_nl_nice",
                points=profile.languages.dutch_nice_to_have_bonus,
                detail="Dutch nice-to-have",
            )
        )
    return components


def _score_education_penalty(text_lower: str, profile: Profile) -> ScoreComponent | None:
    """−20 si Master mandatory, −5 si Bachelor required. Désamorcé par 'or equivalent'."""
    edu = profile.education

    if _regex_search(edu.master_with_alternative_patterns, text_lower):
        return None  # alternative présente → ni penalty Master ni Bachelor

    if _regex_search(edu.master_mandatory_patterns, text_lower):
        return ScoreComponent(
            rule="penalty_master_mandatory",
            points=edu.master_mandatory_penalty,
            detail="Master required",
        )
    if _regex_search(edu.bachelor_required_patterns, text_lower):
        return ScoreComponent(
            rule="penalty_bachelor_required",
            points=edu.bachelor_required_penalty,
            detail="Bachelor required",
        )
    return None


def _score_experience_3y_penalty(text_lower: str, profile: Profile) -> ScoreComponent | None:
    """−10 si '3+ years' ou équivalent (5+ déjà éliminé par les filters)."""
    matched = _regex_search(profile.experience.penalty_3y_patterns, text_lower)
    if matched is None:
        return None
    return ScoreComponent(
        rule="penalty_3y",
        points=profile.experience.penalty_3y,
        detail=matched,
    )


# ─── Aggregator public ───────────────────────────────────────────────────


def score_job(job: JobBase, profile: Profile) -> ScoreResult:
    """Calcule le score d'une offre. Retourne 0 si rejetée par les filters."""
    filter_result = apply_filters(job, profile)
    if filter_result.is_rejected:
        return ScoreResult(
            score=0,
            raw_score=0,
            is_rejected=True,
            rejection_reasons=filter_result.reasons,
            matched_keywords=[],
            breakdown=[],
        )

    text_lower = f"{job.title}\n{job.description}".lower()
    components: list[ScoreComponent] = []
    matched_keywords: list[str] = []

    if c := _score_target_titles(job.title, profile):
        components.append(c)
    if c := _score_junior(text_lower, profile):
        components.append(c)
    if c := _score_graduate(text_lower, profile):
        components.append(c)

    tech_components, tech_kw = _score_technical_keywords(text_lower, profile)
    components.extend(tech_components)
    matched_keywords.extend(tech_kw)

    if c := _score_location(job.location, profile):
        components.append(c)
    components.extend(_score_languages(text_lower, profile))
    if c := _score_education_penalty(text_lower, profile):
        components.append(c)
    if c := _score_experience_3y_penalty(text_lower, profile):
        components.append(c)

    raw = sum(c.points for c in components)
    bounds = profile.score_bounds
    clamped = max(bounds.min, min(bounds.max, raw))

    breakdown: list[dict[str, Any]] = [c.model_dump() for c in components]

    return ScoreResult(
        score=clamped,
        raw_score=raw,
        is_rejected=False,
        rejection_reasons=[],
        matched_keywords=matched_keywords,
        breakdown=breakdown,
    )
