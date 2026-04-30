"""Vue Liste — tableau filtrable + Top 5 cartes."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from dashboard.data import JobRow, is_new_since
from dashboard.format import (
    country_flag,
    humanize_age,
    score_color,
    source_emoji,
    truncate,
)


def render(
    filtered_rows: list[JobRow],
    total: int,
    new_cutoff: datetime | None = None,
) -> None:
    st.markdown("## 📋 Offres cyber junior")
    st.caption(f"{len(filtered_rows)} / {total} offres affichées après filtres")

    if not filtered_rows:
        st.info("Aucune offre ne correspond aux filtres. Ajuste la sidebar.")
        return

    if len(filtered_rows) >= 1:
        st.markdown("### 🏆 Top des offres affichées")
        top5 = filtered_rows[:5]
        cols = st.columns(min(5, len(top5)))
        for col, row in zip(cols, top5, strict=False):
            fg, bg = score_color(row.score)
            with col:
                st.markdown(
                    f"""
                    <div style="background:{bg};border-radius:10px;padding:14px;height:100%;">
                      <div style="font-size:28px;font-weight:800;color:{fg};">{row.score}</div>
                      <div style="font-size:13px;font-weight:600;margin-top:4px;color:#222;">
                        {truncate(row.title, 50)}
                      </div>
                      <div style="font-size:12px;color:#555;margin-top:4px;">
                        {source_emoji(row.source)} {row.company} ·
                        {country_flag(row.country)} {row.location or "-"}
                      </div>
                      <div style="margin-top:8px;">
                        <a href="{row.url}" target="_blank" style="font-size:12px;color:#0d6efd;">
                          ↗ ouvrir l'offre
                        </a>
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    df = pd.DataFrame(
        [
            {
                "🆕": "🆕" if (new_cutoff and is_new_since(r, new_cutoff)) else "",
                "Score": r.score,
                "Source": f"{source_emoji(r.source)} {r.source}",
                "Pays": f"{country_flag(r.country)} {r.country}",
                "Titre": truncate(r.title, 70),
                "Société": truncate(r.company, 25),
                "Localisation": r.location or "-",
                "Découvert": humanize_age(r.first_seen_at),
                "URL": r.url,
            }
            for r in filtered_rows
        ]
    )

    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=min(800, 100 + 35 * len(df)),
        column_config={
            "🆕": st.column_config.TextColumn("🆕", width="small", help="Découverte depuis le run précédent"),
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d", width="small",
            ),
            "URL": st.column_config.LinkColumn(
                "Lien", display_text="↗ ouvrir", width="small",
            ),
            "Titre": st.column_config.TextColumn(width="large"),
            "Société": st.column_config.TextColumn(width="small"),
            "Découvert": st.column_config.TextColumn(width="small"),
        },
        on_select="rerun",
        selection_mode="single-row",
        key="listing_dataframe",
    )

    # Synchronise la sélection avec l'onglet Détail (clé partagée avec le selectbox).
    # Streamlit ne permet pas de basculer d'onglet par code : un message guide l'utilisateur.
    selected_rows = (event.selection.rows if event and event.selection else []) or []
    if selected_rows:
        idx = selected_rows[0]
        if 0 <= idx < len(filtered_rows):
            picked = filtered_rows[idx]
            st.session_state["detail_selected_id"] = picked
            st.info(
                f"✨ **{picked.title}** sélectionnée — clique sur l'onglet "
                f"**🔎 Détail** ci-dessus pour voir le breakdown complet.",
                icon="👉",
            )
