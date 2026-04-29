"""Tests des helpers internes des vues Stats (purs, sans Streamlit runtime)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from dashboard.data import JobRow
from dashboard.views.stats import (
    _by_country,
    _by_source,
    _score_distribution,
    _top_keywords,
    _top_rejection_reasons,
)


def _row(score=50, source="nviso", country="BE", is_rejected=False,
         matched_keywords=None, rejection_reasons=None) -> JobRow:  # type: ignore[no-untyped-def]
    base = datetime.now(timezone.utc)
    return JobRow(
        id=1, source=source, company="C", title="T", location="L",
        country=country, url="https://x.test", description="", score=score,
        is_rejected=is_rejected, is_active=True,
        rejection_reasons=rejection_reasons or [],
        matched_keywords=matched_keywords or [],
        breakdown=[], raw_data={},
        first_seen_at=base, last_seen_at=base, scraped_at=base, posted_at=None,
    )


def test_score_distribution_buckets():
    rows = [_row(score=5), _row(score=15), _row(score=25), _row(score=100)]
    df = _score_distribution(rows)
    assert isinstance(df, pd.DataFrame)
    # 4 buckets ont des entrées : 0-9, 10-19, 20-29, 100
    non_zero = df[df["Offres"] > 0]
    assert len(non_zero) == 4


def test_score_distribution_clamps_above_100():
    """Si quelqu'un fournit score > 100 (impossible normalement), on clamp au dernier bucket."""
    rows = [_row(score=999)]
    df = _score_distribution(rows)
    assert df["Offres"].sum() == 1


def test_by_source_counts_and_sort():
    rows = [
        _row(source="nviso"), _row(source="nviso"), _row(source="nviso"),
        _row(source="easi"), _row(source="easi"),
        _row(source="remotive"),
    ]
    df = _by_source(rows)
    assert df["Offres"].iloc[0] == 3   # nviso top
    assert df["Offres"].iloc[1] == 2   # easi
    assert df["Offres"].iloc[2] == 1   # remotive


def test_by_country_counts():
    rows = [_row(country="BE"), _row(country="BE"), _row(country="LU")]
    df = _by_country(rows)
    assert df["Offres"].iloc[0] == 2   # BE
    assert df["Offres"].iloc[1] == 1   # LU


def test_top_rejection_reasons_only_rejected():
    rows = [
        _row(is_rejected=True, rejection_reasons=["senior_required"]),
        _row(is_rejected=True, rejection_reasons=["senior_required", "dutch_required"]),
        _row(is_rejected=False, rejection_reasons=[]),  # ignoré
    ]
    df = _top_rejection_reasons(rows)
    assert df.loc["senior_required", "Occurrences"] == 2
    assert df.loc["dutch_required", "Occurrences"] == 1


def test_top_rejection_reasons_empty_returns_empty_df():
    assert _top_rejection_reasons([]).empty
    assert _top_rejection_reasons([_row(is_rejected=False)]).empty


def test_top_rejection_reasons_falls_back_to_other_when_empty_list():
    """Une offre flagged is_rejected mais sans raisons listées → 'other'."""
    rows = [_row(is_rejected=True, rejection_reasons=[])]
    df = _top_rejection_reasons(rows)
    assert "other" in df.index


def test_top_keywords_orders_by_frequency():
    rows = [
        _row(matched_keywords=["python", "linux"]),
        _row(matched_keywords=["python", "azure"]),
        _row(matched_keywords=["python"]),
    ]
    df = _top_keywords(rows, limit=10)
    assert df.iloc[0].name == "python"
    assert df.loc["python", "Occurrences"] == 3


def test_top_keywords_respects_limit():
    rows = [_row(matched_keywords=[f"kw{i}" for i in range(20)])]
    df = _top_keywords(rows, limit=5)
    assert len(df) == 5


def test_top_keywords_empty():
    assert _top_keywords([]).empty
