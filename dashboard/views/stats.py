"""Vue Stats — distributions agrégées sur les offres scrapées."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import streamlit as st

from dashboard.data import JobRow
from dashboard.format import country_flag, source_emoji


def _score_distribution(rows: list[JobRow]) -> pd.DataFrame:
    """Histogramme des scores en buckets de 10."""
    buckets = [0] * 11  # 0..100 par 10
    for r in rows:
        idx = min(r.score // 10, 10)
        buckets[idx] += 1
    labels = [f"{i*10}-{i*10+9}" if i < 10 else "100" for i in range(11)]
    return pd.DataFrame({"Score": labels, "Offres": buckets}).set_index("Score")


def _by_source(rows: list[JobRow]) -> pd.DataFrame:
    counts = Counter(r.source for r in rows)
    return pd.DataFrame(
        {"Source": [f"{source_emoji(s)} {s}" for s in counts.keys()],
         "Offres": list(counts.values())},
    ).set_index("Source").sort_values("Offres", ascending=False)


def _by_country(rows: list[JobRow]) -> pd.DataFrame:
    counts = Counter(r.country for r in rows)
    return pd.DataFrame(
        {"Pays": [f"{country_flag(c)} {c}" for c in counts.keys()],
         "Offres": list(counts.values())},
    ).set_index("Pays").sort_values("Offres", ascending=False)


def _top_rejection_reasons(rows: list[JobRow]) -> pd.DataFrame:
    counts: Counter[str] = Counter()
    for r in rows:
        if r.is_rejected:
            counts.update(r.rejection_reasons or ["other"])
    if not counts:
        return pd.DataFrame()
    return pd.DataFrame(
        {"Raison": list(counts.keys()), "Occurrences": list(counts.values())}
    ).set_index("Raison").sort_values("Occurrences", ascending=False)


def _top_keywords(rows: list[JobRow], limit: int = 15) -> pd.DataFrame:
    counts: Counter[str] = Counter()
    for r in rows:
        counts.update(r.matched_keywords)
    if not counts:
        return pd.DataFrame()
    top = counts.most_common(limit)
    return pd.DataFrame(
        {"Keyword": [k for k, _ in top], "Occurrences": [c for _, c in top]}
    ).set_index("Keyword")


def render(filtered_rows: list[JobRow], all_rows: list[JobRow]) -> None:
    st.markdown("## 📊 Statistiques")
    st.caption(
        f"Filtrées : **{len(filtered_rows)}** · Total DB : **{len(all_rows)}**"
    )

    if not all_rows:
        st.info("Pas de données. Lance d'abord un scrape.")
        return

    # ─── Onglets internes pour ne pas surcharger la page ─────────────────

    tab_score, tab_sources, tab_rejets, tab_keywords = st.tabs(
        ["📈 Scores", "📦 Sources & pays", "🚫 Rejets", "🏷️ Keywords"]
    )

    # ── Scores ──────────────────────────────────────────────────────────

    with tab_score:
        st.markdown("#### Distribution des scores (offres filtrées)")
        score_df = _score_distribution([r for r in filtered_rows if not r.is_rejected])
        st.bar_chart(score_df, height=320, color="#0d6efd")

        scored = [r for r in filtered_rows if not r.is_rejected]
        if scored:
            avg = sum(r.score for r in scored) / len(scored)
            top = max(r.score for r in scored)
            c1, c2, c3 = st.columns(3)
            c1.metric("Offres scorées", len(scored))
            c2.metric("Score moyen", f"{avg:.1f}")
            c3.metric("Score max", top)

    # ── Sources & pays ──────────────────────────────────────────────────

    with tab_sources:
        col_src, col_country = st.columns(2)
        with col_src:
            st.markdown("#### Par source")
            st.bar_chart(_by_source(filtered_rows), height=320, color="#1f7a3a")
        with col_country:
            st.markdown("#### Par pays")
            st.bar_chart(_by_country(filtered_rows), height=320, color="#856404")

    # ── Rejets ──────────────────────────────────────────────────────────

    with tab_rejets:
        st.markdown("#### Pourquoi des offres sont-elles rejetées ?")
        rej_df = _top_rejection_reasons(all_rows)  # base totale, pas filtrée
        if rej_df.empty:
            st.success("Aucune offre rejetée dans la base.")
        else:
            st.bar_chart(rej_df, height=320, color="#856404")
            total_rejected = rej_df["Occurrences"].sum()
            total_jobs = len(all_rows)
            st.caption(
                f"{total_rejected} raisons de rejet sur {total_jobs} offres "
                f"({100*total_rejected/total_jobs:.1f} % du total)"
            )

    # ── Keywords ────────────────────────────────────────────────────────

    with tab_keywords:
        st.markdown("#### Top keywords cyber matchés (offres filtrées)")
        kw_df = _top_keywords(filtered_rows)
        if kw_df.empty:
            st.info(
                "Aucun keyword tech matché dans le périmètre filtré. "
                "Essaie d'élargir les filtres ou de relancer le scraping."
            )
        else:
            st.bar_chart(kw_df, height=380, color="#0d6efd")
            st.caption(
                "Keywords présents dans `config/profile.yaml` "
                "(catégories defensive, offensive, scripting, systems, cloud, grc, siem)."
            )
