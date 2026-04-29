"""Tests des helpers data du dashboard (filter, sort, stats, format)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dashboard.data import (
    KEYWORD_CATEGORIES,
    JobRow,
    collect_all_matched_keywords,
    compute_stats,
    filter_new_only,
    filter_rows,
    get_new_offers_cutoff,
    is_new_since,
    load_all_jobs_with_latest_score,
    sort_rows,
)
from src.models import ScrapeRun
from src.config import load_profile
from dashboard.format import (
    country_flag,
    humanize_age,
    score_badge_html,
    score_color,
    source_emoji,
    truncate,
)
from src.models import Country, Job, JobBase, JobSource, ScoreResult
from src.storage import JobRepository


@pytest.fixture
def repo(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    r = JobRepository(db_url=f"sqlite:///{db_path}")
    r.create_all()
    yield r
    r.engine.dispose()


def _row(
    score: int = 50, source: str = "nviso", country: str = "BE",
    title: str = "SOC Junior", company: str = "ACME", location: str = "Brussels",
    is_rejected: bool = False, is_active: bool = True, days_old: int = 0,
    matched_keywords: list[str] | None = None,
    description: str = "",
    breakdown: list[dict] | None = None,
) -> JobRow:
    base = datetime.now(timezone.utc) - timedelta(days=days_old)
    return JobRow(
        id=1, source=source, company=company, title=title,
        location=location, country=country, url="https://x.test/j",
        description=description,
        score=score, is_rejected=is_rejected, is_active=is_active,
        rejection_reasons=[],
        matched_keywords=matched_keywords or [],
        breakdown=breakdown or [],
        raw_data={},
        first_seen_at=base, last_seen_at=base, scraped_at=base, posted_at=None,
    )


# ─── filter_rows ─────────────────────────────────────────────────────────


def test_filter_min_score():
    rows = [_row(score=80), _row(score=40)]
    out = filter_rows(rows, min_score=60)
    assert len(out) == 1
    assert out[0].score == 80


def test_filter_sources_set():
    rows = [_row(source="nviso"), _row(source="easi"), _row(source="remotive")]
    out = filter_rows(rows, sources={"nviso", "easi"})
    assert {r.source for r in out} == {"nviso", "easi"}


def test_filter_only_active():
    rows = [_row(is_active=True), _row(is_active=False)]
    assert len(filter_rows(rows, only_active=True)) == 1
    assert len(filter_rows(rows, only_active=False)) == 2


def test_filter_hide_rejected():
    rows = [_row(is_rejected=False), _row(is_rejected=True)]
    assert len(filter_rows(rows, hide_rejected=True)) == 1
    assert len(filter_rows(rows, hide_rejected=False)) == 2


def test_filter_search_matches_title_and_company():
    rows = [
        _row(title="SOC Analyst", company="NVISO"),
        _row(title="Backend dev", company="Other Co"),
    ]
    assert len(filter_rows(rows, search_text="soc")) == 1
    assert len(filter_rows(rows, search_text="other")) == 1
    assert len(filter_rows(rows, search_text="zzz")) == 0


def test_filter_combined():
    rows = [
        _row(score=70, source="nviso", is_rejected=False),
        _row(score=70, source="nviso", is_rejected=True),
        _row(score=20, source="nviso", is_rejected=False),
    ]
    out = filter_rows(rows, min_score=60, hide_rejected=True)
    assert len(out) == 1


def test_filter_empty_sources_set_excludes_all():
    """Si l'utilisateur déselectionne tout : aucune source → 0 résultat."""
    rows = [_row(source="nviso")]
    assert filter_rows(rows, sources=set()) == rows  # set vide = pas de filtre


# ─── Nouveaux filtres ────────────────────────────────────────────────────


def test_filter_max_score():
    rows = [_row(score=85), _row(score=50), _row(score=10)]
    out = filter_rows(rows, max_score=70)
    assert {r.score for r in out} == {50, 10}


def test_filter_score_range():
    rows = [_row(score=10), _row(score=50), _row(score=85)]
    out = filter_rows(rows, min_score=40, max_score=70)
    assert [r.score for r in out] == [50]


def test_filter_discovered_within_days():
    rows = [_row(days_old=0), _row(days_old=3), _row(days_old=10)]
    out = filter_rows(rows, discovered_within_days=7)
    assert len(out) == 2  # 0 et 3 jours, pas 10


def test_filter_discovered_zero_disabled():
    """0 ou None = pas de filtre date."""
    rows = [_row(days_old=0), _row(days_old=999)]
    assert len(filter_rows(rows, discovered_within_days=None)) == 2
    assert len(filter_rows(rows, discovered_within_days=0)) == 2


def test_filter_matched_keywords_any():
    rows = [
        _row(matched_keywords=["python", "linux"]),
        _row(matched_keywords=["azure"]),
        _row(matched_keywords=[]),
    ]
    out = filter_rows(rows, matched_keywords_any={"python"})
    assert len(out) == 1
    out = filter_rows(rows, matched_keywords_any={"python", "azure"})
    assert len(out) == 2


def test_filter_matched_keywords_case_insensitive():
    rows = [_row(matched_keywords=["Python", "Linux"])]
    out = filter_rows(rows, matched_keywords_any={"python"})
    assert len(out) == 1


def test_filter_keyword_categories_requires_profile():
    """Sans profil, le filtre catégorie est ignoré."""
    rows = [_row(matched_keywords=["wazuh"])]
    out = filter_rows(rows, keyword_categories_any={"defensive"}, profile=None)
    assert len(out) == 1


def test_filter_keyword_categories_with_profile():
    profile = load_profile()
    rows = [
        _row(matched_keywords=["wazuh"]),       # defensive
        _row(matched_keywords=["metasploit"]),  # offensive
        _row(matched_keywords=["python"]),      # scripting
    ]
    out = filter_rows(
        rows, keyword_categories_any={"defensive"}, profile=profile
    )
    assert len(out) == 1
    assert "wazuh" in out[0].matched_keywords

    out2 = filter_rows(
        rows, keyword_categories_any={"defensive", "offensive"}, profile=profile
    )
    assert len(out2) == 2


# ─── collect_all_matched_keywords ────────────────────────────────────────


def test_collect_keywords_dedupe_sort():
    rows = [
        _row(matched_keywords=["python", "linux"]),
        _row(matched_keywords=["python", "azure"]),
    ]
    assert collect_all_matched_keywords(rows) == ["azure", "linux", "python"]


def test_collect_keywords_empty():
    assert collect_all_matched_keywords([]) == []
    assert collect_all_matched_keywords([_row()]) == []


def test_keyword_categories_match_profile_attrs():
    """Sécurité : les catégories listées dans KEYWORD_CATEGORIES doivent toutes exister sur Profile.technical_keywords."""
    profile = load_profile()
    for cat in KEYWORD_CATEGORIES:
        assert hasattr(profile.technical_keywords, cat), f"missing {cat}"


# ─── sort_rows ───────────────────────────────────────────────────────────


def test_sort_by_score_desc():
    rows = [_row(score=50), _row(score=80), _row(score=20)]
    out = sort_rows(rows, by="score")
    assert [r.score for r in out] == [80, 50, 20]


def test_sort_by_recent():
    rows = [
        _row(score=50, days_old=2),
        _row(score=50, days_old=0),
        _row(score=50, days_old=1),
    ]
    out = sort_rows(rows, by="recent")
    assert out[0].scraped_at > out[1].scraped_at > out[2].scraped_at


# ─── compute_stats ───────────────────────────────────────────────────────


def test_compute_stats_basic():
    rows = [
        _row(score=80, is_rejected=False, source="nviso"),
        _row(score=20, is_rejected=False, source="easi"),
        _row(score=0, is_rejected=True, source="easi"),
    ]
    stats = compute_stats(rows)
    assert stats.total_jobs == 3
    assert stats.active_jobs == 3
    assert stats.scored_jobs == 2
    assert stats.rejected_jobs == 1
    assert stats.top_score == 80
    assert stats.avg_score == 50.0
    assert stats.sources_count == {"nviso": 1, "easi": 2}


def test_compute_stats_empty():
    stats = compute_stats([])
    assert stats.top_score == 0
    assert stats.avg_score == 0.0


# ─── load_all_jobs_with_latest_score ─────────────────────────────────────


def test_load_jobs_with_score(repo: JobRepository):
    j_in = JobBase(
        source=JobSource.NVISO, external_id="x1", title="SOC Junior",
        company="NVISO", location="Brussels", country=Country.BE,
        url="https://x.test/1", description="junior",
    )
    job, _ = repo.upsert_job(j_in)
    assert job.id is not None
    repo.save_score(ScoreResult(score=72, raw_score=72, job_id=job.id, breakdown=[]))
    # Score plus récent : doit être pris
    repo.save_score(ScoreResult(score=85, raw_score=85, job_id=job.id, breakdown=[]))

    rows = load_all_jobs_with_latest_score(repo)
    assert len(rows) == 1
    assert rows[0].score == 85  # le dernier
    assert rows[0].title == "SOC Junior"


def test_load_jobs_no_score_yet(repo: JobRepository):
    j_in = JobBase(
        source=JobSource.NVISO, external_id="x2", title="X",
        company="C", country=Country.BE, url="https://x.test/2", description="",
    )
    repo.upsert_job(j_in)
    rows = load_all_jobs_with_latest_score(repo)
    assert len(rows) == 1
    assert rows[0].score == 0  # pas de ScoreResult → 0
    assert rows[0].is_rejected is False


# ─── format helpers ──────────────────────────────────────────────────────


def test_score_color_thresholds():
    assert score_color(85) == score_color(90)  # green band
    assert score_color(60) != score_color(85)
    assert score_color(40) != score_color(60)


def test_score_badge_html_contains_score():
    html = score_badge_html(72)
    assert ">72<" in html
    assert "background:" in html
    assert "color:" in html


def test_humanize_age():
    now = datetime.now(timezone.utc)
    assert "instant" in humanize_age(now - timedelta(seconds=10), now=now)
    assert "min" in humanize_age(now - timedelta(minutes=5), now=now)
    assert "h" in humanize_age(now - timedelta(hours=3), now=now)
    assert "j" in humanize_age(now - timedelta(days=2), now=now)
    assert "mois" in humanize_age(now - timedelta(days=60), now=now)


def test_humanize_age_naive_datetime_is_safe():
    naive = datetime(2026, 4, 1, 12, 0)  # pas de tzinfo
    out = humanize_age(naive, now=datetime.now(timezone.utc))
    assert isinstance(out, str)


def test_truncate():
    assert truncate("abc", 10) == "abc"
    out = truncate("a" * 100, 10)
    assert out.endswith("…")
    assert len(out) <= 10


def test_source_emoji_known_and_unknown():
    assert source_emoji("nviso") == "🛡️"
    assert source_emoji("xyz_unknown") == "📄"


def test_country_flag_known_and_unknown():
    assert country_flag("BE") == "🇧🇪"
    assert country_flag("XX") == "🌍"


# ─── is_new_since ────────────────────────────────────────────────────────


def test_is_new_since():
    now = datetime.now(timezone.utc)
    fresh = _row(days_old=0)
    old = _row(days_old=5)
    cutoff = now - timedelta(hours=1)
    assert is_new_since(fresh, cutoff) is True
    assert is_new_since(old, cutoff) is False


def test_is_new_since_naive_datetime_safe():
    """is_new_since gère les datetimes naive sans crasher."""
    fresh = _row(days_old=0)
    naive_cutoff = datetime(2020, 1, 1)  # tzinfo None
    assert is_new_since(fresh, naive_cutoff) is True


# ─── Run history & cutoff ────────────────────────────────────────────────


def test_get_new_offers_cutoff_no_runs(tmp_path):
    """0 ou 1 run en DB → None (toutes les offres sont 'nouvelles')."""
    from src.storage import JobRepository

    repo = JobRepository(db_url=f"sqlite:///{tmp_path / 'a.db'}")
    repo.create_all()
    try:
        assert get_new_offers_cutoff(repo) is None
        # 1 run only → toujours None
        repo.save_run(
            ScrapeRun(started_at=datetime.now(timezone.utc), sources_run=[])
        )
        assert get_new_offers_cutoff(repo) is None
    finally:
        repo.engine.dispose()


def test_get_new_offers_cutoff_uses_previous_run(tmp_path):
    """Avec 2+ runs, le cutoff = started_at de l'avant-dernier."""
    from src.storage import JobRepository

    repo = JobRepository(db_url=f"sqlite:///{tmp_path / 'b.db'}")
    repo.create_all()
    try:
        t1 = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)
        repo.save_run(ScrapeRun(started_at=t1, sources_run=[]))
        repo.save_run(ScrapeRun(started_at=t2, sources_run=[]))
        assert get_new_offers_cutoff(repo) == t1  # avant-dernier
    finally:
        repo.engine.dispose()


def test_filter_new_only():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)
    rows = [_row(days_old=0), _row(days_old=2)]  # 1 fresh, 1 old
    assert len(filter_new_only(rows, cutoff)) == 1
    assert len(filter_new_only(rows, None)) == 2  # None = tout passe
