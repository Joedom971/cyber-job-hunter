"""Helpers de formatage pour l'affichage Streamlit (badges, dates, urls)."""

from __future__ import annotations

from datetime import datetime, timezone


# Couleurs WCAG AA (contraste >= 4.5:1) — identiques au mockup email.
SCORE_GREEN = "#1f7a3a"
SCORE_GREEN_BG = "#d4edda"
SCORE_ORANGE = "#856404"
SCORE_ORANGE_BG = "#fff3cd"
SCORE_GRAY = "#383d41"
SCORE_GRAY_BG = "#e2e3e5"


def score_color(score: int) -> tuple[str, str]:
    """Retourne (text_color, bg_color) selon le score."""
    if score >= 80:
        return SCORE_GREEN, SCORE_GREEN_BG
    if score >= 60:
        return SCORE_ORANGE, SCORE_ORANGE_BG
    return SCORE_GRAY, SCORE_GRAY_BG


def score_badge_html(score: int) -> str:
    """HTML inline pour un badge score (utilisable dans st.markdown(unsafe_allow_html=True))."""
    fg, bg = score_color(score)
    return (
        f'<span style="display:inline-block;min-width:36px;text-align:center;'
        f'background:{bg};color:{fg};padding:3px 8px;border-radius:6px;'
        f'font-weight:700;font-size:13px">{score}</span>'
    )


def humanize_age(dt: datetime, now: datetime | None = None) -> str:
    """Renvoie 'il y a Nh / Nj' pour un datetime relatif. Robuste TZ-aware/naive."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "à l'instant"
    if seconds < 3600:
        m = seconds // 60
        return f"il y a {m} min"
    if seconds < 86400:
        h = seconds // 3600
        return f"il y a {h}h"
    days = seconds // 86400
    if days < 30:
        return f"il y a {days}j"
    months = days // 30
    return f"il y a {months} mois"


def truncate(text: str, max_len: int = 80, ellipsis: str = "…") -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + ellipsis


def source_emoji(source: str) -> str:
    """Petit visual pour distinguer les sources d'un coup d'œil."""
    return {
        "remotive": "🌍",
        "nviso": "🛡️",
        "itsme": "🔐",
        "easi": "💼",
        "other": "📄",
    }.get(source, "📄")


def country_flag(country: str) -> str:
    return {
        "BE": "🇧🇪",
        "LU": "🇱🇺",
        "FR": "🇫🇷",
        "NL": "🇳🇱",
        "DE": "🇩🇪",
        "IE": "🇮🇪",
        "REMOTE": "🌐",
        "OTHER": "🌍",
    }.get(country, "🌍")
