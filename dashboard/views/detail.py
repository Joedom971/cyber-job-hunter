"""Vue Détail — drill-down sur une offre avec breakdown scoring complet."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from dashboard.data import JobRow
from dashboard.format import (
    country_flag,
    humanize_age,
    score_badge_html,
    source_emoji,
)


def _format_option(r: JobRow) -> str:
    flag = country_flag(r.country)
    src = source_emoji(r.source)
    return f"[{r.score:>3}] {src} {r.company[:18]} · {r.title[:55]}  {flag}"


# ─── Helpers de rendu (HTML inline) ──────────────────────────────────────


# Catégorisation rapide des keywords pour colorer les chips
_KW_CATEGORY_COLORS: dict[str, tuple[str, str]] = {
    # (background, text)
    "defensive":    ("#d4edda", "#1f7a3a"),
    "offensive":    ("#f8d7da", "#a02334"),
    "scripting":    ("#cfe2ff", "#0a3d80"),
    "systems":      ("#e2e3e5", "#383d41"),
    "cloud":        ("#cff4fc", "#055160"),
    "grc":          ("#fff3cd", "#856404"),
    "siem_tools":   ("#e7d6f5", "#5a2d8a"),
    "default":      ("#e9ecef", "#495057"),
}

# Mots-clés très courts → category mapping rapide (pas besoin du profil)
_KW_TO_CATEGORY: dict[str, str] = {
    # defensive
    "active directory": "defensive", "wazuh": "defensive", "sysmon": "defensive",
    "wireshark": "defensive", "bloodhound": "defensive", "splunk": "defensive",
    "mitre att&ck": "defensive", "mitre attack": "defensive",
    "threat hunting": "defensive", "incident response": "defensive",
    "threat intelligence": "defensive", "cti": "defensive", "osint": "defensive",
    "blue team": "defensive", "purple team": "defensive",
    # offensive
    "metasploit": "offensive", "burp suite": "offensive", "nmap": "offensive",
    "sqlmap": "offensive", "hydra": "offensive", "kerberoasting": "offensive",
    "red team": "offensive", "penetration testing": "offensive",
    "pentesting": "offensive", "owasp": "offensive",
    # scripting
    "python": "scripting", "bash": "scripting", "powershell": "scripting",
    "regex": "scripting",
    # systems
    "linux": "systems", "windows": "systems", "macos": "systems",
    "docker": "systems", "kubernetes": "systems", "git": "systems",
    # cloud
    "azure": "cloud", "aws": "cloud", "gcp": "cloud", "iam": "cloud",
    "azure ad": "cloud", "entra id": "cloud",
    # grc
    "iso 27001": "grc", "iso 27002": "grc", "iso 27005": "grc",
    "nis2": "grc", "gdpr": "grc", "rgpd": "grc", "dora": "grc",
    "ai act": "grc", "nist csf": "grc", "soc 2": "grc", "pci dss": "grc",
    # siem
    "siem": "siem_tools", "soc": "siem_tools", "edr": "siem_tools",
    "xdr": "siem_tools", "ids": "siem_tools", "ips": "siem_tools",
    "cybersecurity": "siem_tools", "cybersécurité": "siem_tools",
}


def _kw_category(kw: str) -> str:
    return _KW_TO_CATEGORY.get(kw.lower(), "default")


def _render_keyword_chips(keywords: list[str]) -> str:
    """Rend les keywords sous forme de chips colorés par catégorie."""
    chips = []
    for kw in keywords:
        cat = _kw_category(kw)
        bg, fg = _KW_CATEGORY_COLORS.get(cat, _KW_CATEGORY_COLORS["default"])
        chips.append(
            f'<span style="display:inline-block;background:{bg};color:{fg};'
            f'padding:3px 10px;border-radius:12px;font-size:12px;'
            f'font-weight:600;margin:2px 4px 2px 0">{kw}</span>'
        )
    return "<div>" + "".join(chips) + "</div>"


def _format_description_html(description: str) -> str:
    """Met en forme la description pour un affichage Markdown agréable.

    Heuristiques :
    - Détecte des sections clés (Description, Profil, Offre, Requirements,
      Qualifications, Skills, Benefits, About, Your role) et les transforme
      en h4
    - Conserve les paragraphes et listes
    - Échappe le HTML existant pour éviter les injections
    """
    import html as _html
    import re as _re

    if not description:
        return ""

    # Échappe le HTML existant (les descriptions sont déjà HTML-strippées
    # côté scraping, mais defense in depth)
    text = _html.escape(description)

    # Détecte les sections (anglais et français)
    section_patterns = [
        r"\b(Job Description|Description|Position description|Function|Fonction)\b",
        r"\b(Your Job|Your role|Votre mission|Vos missions)\b",
        r"\b(Profile|Profil|Your Profile|Your team|Votre équipe)\b",
        r"\b(Qualifications?|Requirements?|Skills?|Compétences|Exigences)\b",
        r"\b(Our offer|Offer|Notre offre|Nous offrons|Aanbod|Offre)\b",
        r"\b(Benefits?|Avantages|Voordelen)\b",
        r"\b(About (?:Accenture|us|the company)|À propos)\b",
        r"\b(Equal Opportunity|Diversity|Equal Employment)\b",
    ]
    for pat in section_patterns:
        text = _re.sub(
            f"({pat})\\s*[:.]?",
            r"\n\n#### \1\n",
            text,
            flags=_re.IGNORECASE,
        )

    # Convertit les puces 'simples' en bullets markdown si elles existent
    text = _re.sub(r"\s*•\s*", "\n- ", text)
    # Liste numérotée si format "1. " "2. "
    # (déjà géré par markdown)

    # Préserve les paragraphes : doubles sauts de ligne
    text = _re.sub(r"\n{3,}", "\n\n", text)

    return text


def render(rows: list[JobRow]) -> None:
    st.markdown("## 🔎 Détail d'une offre")

    if not rows:
        st.info("Aucune offre à afficher. Ajuste les filtres dans la sidebar.")
        return

    selected = st.selectbox(
        "Sélectionne une offre",
        options=rows,
        format_func=_format_option,
        index=0,
        key="detail_selected_id",
    )
    if selected is None:
        return

    job: JobRow = selected

    # ─── En-tête ──────────────────────────────────────────────────────────

    col_main, col_score = st.columns([4, 1])
    with col_main:
        st.markdown(f"### {job.title}")
        st.markdown(
            f"**{source_emoji(job.source)} {job.company}** · "
            f"{country_flag(job.country)} {job.country} · "
            f"{job.location or 'remote'}"
        )
        st.markdown(f"[↗ Ouvrir l'offre]({job.url})")
    with col_score:
        st.markdown(score_badge_html(job.score), unsafe_allow_html=True)
        if job.is_rejected:
            st.error("REJETÉE", icon="🚫")

    # ─── Métadonnées timeline ─────────────────────────────────────────────

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Score", f"{job.score}/100")
    c2.metric(
        "Découverte",
        humanize_age(job.first_seen_at),
        help=job.first_seen_at.isoformat(),
    )
    c3.metric(
        "Dernier scrape",
        humanize_age(job.scraped_at),
        help=job.scraped_at.isoformat(),
    )
    c4.metric(
        "Publication",
        humanize_age(job.posted_at) if job.posted_at else "—",
        help=job.posted_at.isoformat() if job.posted_at else "Non communiquée",
    )

    st.markdown("---")

    # ─── Breakdown scoring ────────────────────────────────────────────────

    if job.breakdown:
        st.markdown("### 🧮 Breakdown du score")
        breakdown_df = pd.DataFrame(
            [
                {
                    "Règle": item.get("rule", ""),
                    "Points": item.get("points", 0),
                    "Détail": item.get("detail", ""),
                }
                for item in job.breakdown
            ]
        )
        total = breakdown_df["Points"].sum()
        st.dataframe(
            breakdown_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Points": st.column_config.NumberColumn(format="%+d"),
            },
        )
        st.caption(
            f"**Somme : {total:+d}** "
            f"→ score final clampé à {job.score}/100"
        )
    elif job.is_rejected:
        st.warning(
            "Cette offre a été **rejetée par les filtres** avant scoring → pas de breakdown."
        )
    else:
        st.info("Pas de breakdown disponible (ScoreResult non calculé).")

    # ─── Rejection reasons ────────────────────────────────────────────────

    if job.rejection_reasons:
        st.markdown("### 🚫 Raisons de rejet")
        st.markdown(", ".join(f"`{r}`" for r in job.rejection_reasons))

    # ─── Matched keywords (chips colorés par catégorie) ──────────────────

    if job.matched_keywords:
        st.markdown("### 🏷️ Keywords cyber matchés")
        st.markdown(_render_keyword_chips(job.matched_keywords), unsafe_allow_html=True)

    # ─── Description (structurée et lisible) ──────────────────────────────

    if job.description:
        st.markdown("### 📝 Description complète")
        st.markdown(
            _format_description_html(job.description),
            unsafe_allow_html=True,
        )

    # ─── Raw data (debug) ─────────────────────────────────────────────────

    if job.raw_data:
        with st.expander("🔧 Raw data (debug)", expanded=False):
            st.code(
                json.dumps(job.raw_data, indent=2, default=str, ensure_ascii=False),
                language="json",
            )
