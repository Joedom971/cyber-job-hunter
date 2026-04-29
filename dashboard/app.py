"""Dashboard principal — page Liste avec filtres.

Lance avec :
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permet d'importer src.* sans installer le package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from dashboard.data import (
    KEYWORD_CATEGORIES,
    JobRow,
    collect_all_matched_keywords,
    compute_stats,
    filter_rows,
    get_new_offers_cutoff,
    is_new_since,
    load_all_jobs_with_latest_score,
    open_repo,
    sort_rows,
)
from dashboard.format import country_flag, source_emoji
from dashboard.views import detail as view_detail
from dashboard.views import listing as view_listing
from dashboard.views import stats as view_stats
from src.config import load_profile

# ─── Page config ─────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Cyber Job Hunter",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─── Data layer (cached) ─────────────────────────────────────────────────


@st.cache_data(ttl=60)
def _load_rows() -> list[JobRow]:
    """Charge toutes les offres avec leur dernier score. Cache 60s."""
    repo = open_repo()
    return load_all_jobs_with_latest_score(repo)


@st.cache_data(ttl=60)
def _load_new_cutoff():  # type: ignore[no-untyped-def]
    """Datetime du run précédent (référence pour le badge 'Nouveau')."""
    return get_new_offers_cutoff(open_repo())


@st.cache_resource
def _load_profile():  # type: ignore[no-untyped-def]
    """Charge le profil depuis profile.yaml une seule fois."""
    return load_profile()


# ─── Reset filtres ───────────────────────────────────────────────────────


_FILTER_KEYS = (
    "flt_score_range",
    "flt_sources",
    "flt_countries",
    "flt_only_active",
    "flt_hide_rejected",
    "flt_search",
    "flt_sort",
    "flt_discovered_days",
    "flt_keywords",
    "flt_categories",
    "flt_new_only",
)


def _reset_filters() -> None:
    for k in _FILTER_KEYS:
        st.session_state.pop(k, None)


# ─── Sidebar ─────────────────────────────────────────────────────────────


_DISCOVERED_OPTIONS = {
    "Toutes": None,
    "24h": 1,
    "7 jours": 7,
    "30 jours": 30,
}


def render_sidebar(all_rows: list[JobRow], new_cutoff) -> dict:  # type: ignore[type-arg, no-untyped-def]
    st.sidebar.markdown("## 🎯 Cyber Job Hunter")
    st.sidebar.caption("Veille auto · Sprint 2")

    # Badge "nouvelles offres" — visible si on a au moins 2 runs en DB
    if new_cutoff is not None:
        new_count = sum(1 for r in all_rows if is_new_since(r, new_cutoff))
        if new_count > 0:
            st.sidebar.success(f"🆕 {new_count} nouvelles depuis le run précédent")

    c_refresh, c_reset = st.sidebar.columns(2)
    if c_refresh.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    if c_reset.button("🧹 Reset", use_container_width=True, help="Réinitialise tous les filtres"):
        _reset_filters()
        st.rerun()

    stats = compute_stats(all_rows)
    st.sidebar.markdown("### 📊 Stats globales")
    c1, c2 = st.sidebar.columns(2)
    c1.metric("Total", stats.total_jobs)
    c2.metric("Actives", stats.active_jobs)
    c1.metric("Scorées", stats.scored_jobs)
    c2.metric("Rejetées", stats.rejected_jobs)
    st.sidebar.metric(
        "Top / Avg score",
        f"{stats.top_score} / {stats.avg_score:.0f}",
    )

    st.sidebar.markdown("### 🔍 Filtres")

    score_range = st.sidebar.slider(
        "Plage de score",
        min_value=0, max_value=100, value=(0, 100), step=5,
        key="flt_score_range",
    )
    min_score, max_score = score_range

    available_sources = sorted(stats.sources_count.keys())
    sources = set(
        st.sidebar.multiselect(
            "Sources",
            options=available_sources,
            default=available_sources,
            format_func=lambda s: f"{source_emoji(s)} {s}  ({stats.sources_count.get(s, 0)})",
            key="flt_sources",
        )
    )

    available_countries = sorted({r.country for r in all_rows if r.is_active})
    countries = set(
        st.sidebar.multiselect(
            "Pays",
            options=available_countries,
            default=available_countries,
            format_func=lambda c: f"{country_flag(c)} {c}",
            key="flt_countries",
        )
    )

    discovered_label = st.sidebar.radio(
        "Découvert depuis",
        options=list(_DISCOVERED_OPTIONS.keys()),
        index=0,
        horizontal=True,
        key="flt_discovered_days",
    )
    discovered_days = _DISCOVERED_OPTIONS[discovered_label]

    # Catégories de keywords — multi-select boutons compacts
    categories = set(
        st.sidebar.multiselect(
            "Catégorie cyber",
            options=list(KEYWORD_CATEGORIES),
            default=[],
            format_func=lambda c: c.replace("_", " ").title(),
            help="Garde les offres qui ont matché au moins 1 keyword de ces catégories",
            key="flt_categories",
        )
    )

    # Liste plate des keywords présents dans les rows actuels (dédupliqués)
    available_keywords = collect_all_matched_keywords(all_rows)
    keywords = set(
        st.sidebar.multiselect(
            "Keyword tech matché",
            options=available_keywords,
            default=[],
            help="Garde les offres qui ont matché au moins 1 de ces keywords",
            key="flt_keywords",
        )
    )

    only_active = st.sidebar.checkbox("Actives uniquement", value=True, key="flt_only_active")
    hide_rejected = st.sidebar.checkbox("Masquer les rejets", value=True, key="flt_hide_rejected")
    new_only = st.sidebar.checkbox(
        "🆕 Uniquement les nouvelles",
        value=False,
        key="flt_new_only",
        help="Garde les offres découvertes après le run précédent",
        disabled=new_cutoff is None,
    )

    search = st.sidebar.text_input("🔎 Recherche titre/société", "", key="flt_search")

    sort_by = st.sidebar.radio(
        "Tri",
        options=["score", "recent", "first_seen"],
        format_func=lambda s: {"score": "Score ↓", "recent": "Plus récent",
                               "first_seen": "Découverte"}[s],
        horizontal=True,
        key="flt_sort",
    )

    return {
        "min_score": min_score,
        "max_score": max_score,
        "sources": sources,
        "countries": countries,
        "only_active": only_active,
        "hide_rejected": hide_rejected,
        "search": search,
        "sort_by": sort_by,
        "discovered_days": discovered_days,
        "matched_keywords": keywords,
        "categories": categories,
        "new_only": new_only,
    }


# ─── Main ────────────────────────────────────────────────────────────────


def main() -> None:
    try:
        all_rows = _load_rows()
    except Exception as e:
        st.error(f"Impossible de charger la base : {e}")
        st.info(
            "💡 Lance d'abord `python scripts/init_db.py` puis "
            "`python scripts/run_scrape.py` pour peupler la DB."
        )
        return

    if not all_rows:
        st.warning("La base est vide.")
        st.code("python scripts/run_scrape.py", language="bash")
        return

    new_cutoff = _load_new_cutoff()
    filters = render_sidebar(all_rows, new_cutoff)
    profile = _load_profile() if filters["categories"] else None
    filtered = filter_rows(
        all_rows,
        min_score=filters["min_score"],
        max_score=filters["max_score"],
        sources=filters["sources"] or None,
        countries=filters["countries"] or None,
        only_active=filters["only_active"],
        hide_rejected=filters["hide_rejected"],
        search_text=filters["search"],
        discovered_within_days=filters["discovered_days"],
        matched_keywords_any=filters["matched_keywords"] or None,
        keyword_categories_any=filters["categories"] or None,
        profile=profile,
    )
    if filters["new_only"] and new_cutoff is not None:
        filtered = [r for r in filtered if is_new_since(r, new_cutoff)]
    sorted_rows = sort_rows(filtered, by=filters["sort_by"])

    st.markdown("# 🎯 Cyber Job Hunter")
    tab_listing, tab_detail, tab_stats = st.tabs(
        ["📋 Liste", "🔎 Détail", "📊 Stats"]
    )
    with tab_listing:
        view_listing.render(sorted_rows, total=len(all_rows), new_cutoff=new_cutoff)
    with tab_detail:
        view_detail.render(sorted_rows)
    with tab_stats:
        view_stats.render(sorted_rows, all_rows)


if __name__ == "__main__":
    main()
else:
    main()  # Streamlit lance le module sans __main__
