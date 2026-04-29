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

    # ─── Matched keywords ─────────────────────────────────────────────────

    if job.matched_keywords:
        st.markdown("### 🏷️ Keywords matchés")
        st.markdown(" ".join(f"`{kw}`" for kw in job.matched_keywords))

    # ─── Description ──────────────────────────────────────────────────────

    if job.description:
        with st.expander("📝 Description complète", expanded=False):
            st.text(job.description)

    # ─── Raw data (debug) ─────────────────────────────────────────────────

    if job.raw_data:
        with st.expander("🔧 Raw data (debug)", expanded=False):
            st.code(
                json.dumps(job.raw_data, indent=2, default=str, ensure_ascii=False),
                language="json",
            )
