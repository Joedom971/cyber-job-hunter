"""Tests storage + déduplication. SQLite sur fichier temporaire (tmp_path)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.deduplication import has_content_changed, merge_incoming
from src.models import Country, Job, JobBase, JobSource, ScoreResult, ScrapeRun
from src.storage import JobRepository


@pytest.fixture
def repo(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    r = JobRepository(db_url=f"sqlite:///{db_path}")
    r.create_all()
    yield r
    r.engine.dispose()


def _make_jobbase(
    external_id: str = "ext-1",
    title: str = "SOC Analyst Junior",
    description: str = "Junior role in Brussels.",
    location: str = "Brussels",
    company: str = "NVISO",
    source: JobSource = JobSource.NVISO,
) -> JobBase:
    return JobBase(
        source=source,
        external_id=external_id,
        title=title,
        description=description,
        location=location,
        country=Country.BE,
        company=company,
        url=f"https://example.com/{external_id}",
    )


# ─── Déduplication (fonctions pures) ─────────────────────────────────────


def test_content_hash_stable():
    h1 = Job.compute_content_hash("Title", "Co", "Loc", "desc")
    h2 = Job.compute_content_hash("Title", "Co", "Loc", "desc")
    assert h1 == h2
    assert len(h1) == 64


def test_content_hash_changes_with_input():
    h1 = Job.compute_content_hash("Title A", "Co", "Loc", "desc")
    h2 = Job.compute_content_hash("Title B", "Co", "Loc", "desc")
    assert h1 != h2


def test_has_content_changed_detects_diff(repo: JobRepository):
    incoming = _make_jobbase()
    job, _ = repo.upsert_job(incoming)

    incoming2 = _make_jobbase(title="SOC Analyst SENIOR")
    assert has_content_changed(job, incoming2) is True


def test_has_content_changed_false_on_identical(repo: JobRepository):
    incoming = _make_jobbase()
    job, _ = repo.upsert_job(incoming)
    same = _make_jobbase()  # même contenu
    assert has_content_changed(job, same) is False


def test_merge_preserves_first_seen_and_id(repo: JobRepository):
    incoming = _make_jobbase(title="Old title")
    job, _ = repo.upsert_job(incoming)
    original_first_seen = job.first_seen_at
    original_id = job.id

    new = _make_jobbase(title="New title")
    merged = merge_incoming(job, new)
    assert merged.id == original_id
    assert merged.first_seen_at == original_first_seen
    assert merged.title == "New title"
    assert merged.is_active is True


# ─── upsert_job ──────────────────────────────────────────────────────────


def test_upsert_first_time_returns_is_new_true(repo: JobRepository):
    job, is_new = repo.upsert_job(_make_jobbase())
    assert is_new is True
    assert job.id is not None
    assert job.first_seen_at is not None


def test_upsert_same_offer_returns_is_new_false(repo: JobRepository):
    repo.upsert_job(_make_jobbase())
    job, is_new = repo.upsert_job(_make_jobbase())
    assert is_new is False


def test_upsert_different_externalid_creates_new(repo: JobRepository):
    repo.upsert_job(_make_jobbase(external_id="a"))
    _, is_new = repo.upsert_job(_make_jobbase(external_id="b"))
    assert is_new is True
    assert repo.count_jobs() == 2


def test_upsert_preserves_first_seen_across_runs(repo: JobRepository):
    job1, _ = repo.upsert_job(_make_jobbase())
    initial_first_seen = job1.first_seen_at

    job2, _ = repo.upsert_job(_make_jobbase(title="Updated title"))
    assert job2.first_seen_at == initial_first_seen
    assert job2.title == "Updated title"
    assert job2.last_seen_at >= initial_first_seen


def test_upsert_reactivates_inactive_job(repo: JobRepository):
    job, _ = repo.upsert_job(_make_jobbase())
    assert job.id is not None
    repo.mark_inactive(JobSource.NVISO, kept_external_ids=set())
    # Re-scrape la même offre
    job2, is_new = repo.upsert_job(_make_jobbase())
    assert is_new is False
    assert job2.is_active is True


# ─── ScoreResult ─────────────────────────────────────────────────────────


def test_save_score_and_retrieve_latest(repo: JobRepository):
    job, _ = repo.upsert_job(_make_jobbase())
    assert job.id is not None

    score = ScoreResult(
        score=72, raw_score=72, is_rejected=False,
        matched_keywords=["python", "siem"], breakdown=[],
        job_id=job.id,
    )
    repo.save_score(score)

    latest = repo.get_latest_score(job.id)
    assert latest is not None
    assert latest.score == 72


def test_get_latest_score_returns_most_recent(repo: JobRepository):
    job, _ = repo.upsert_job(_make_jobbase())
    assert job.id is not None

    repo.save_score(ScoreResult(score=50, raw_score=50, job_id=job.id, breakdown=[]))
    repo.save_score(ScoreResult(score=80, raw_score=80, job_id=job.id, breakdown=[]))

    latest = repo.get_latest_score(job.id)
    assert latest is not None
    assert latest.score == 80


# ─── get_recent_jobs ─────────────────────────────────────────────────────


def test_get_recent_jobs_filters_by_min_score(repo: JobRepository):
    j1, _ = repo.upsert_job(_make_jobbase(external_id="hi"))
    j2, _ = repo.upsert_job(_make_jobbase(external_id="lo"))
    assert j1.id and j2.id

    repo.save_score(ScoreResult(score=85, raw_score=85, job_id=j1.id, breakdown=[]))
    repo.save_score(ScoreResult(score=30, raw_score=30, job_id=j2.id, breakdown=[]))

    high = repo.get_recent_jobs(since_hours=24, min_score=60)
    assert len(high) == 1
    assert high[0].external_id == "hi"


def test_get_recent_jobs_no_score_filter_includes_all(repo: JobRepository):
    repo.upsert_job(_make_jobbase(external_id="a"))
    repo.upsert_job(_make_jobbase(external_id="b"))
    all_recent = repo.get_recent_jobs(since_hours=24, min_score=0)
    assert len(all_recent) == 2


# ─── get_new_jobs_since ──────────────────────────────────────────────────


def test_get_new_jobs_since_filter(repo: JobRepository):
    repo.upsert_job(_make_jobbase(external_id="a"))
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)
    # Le job 'a' a first_seen_at < cutoff → doit être exclu
    new_jobs = repo.get_new_jobs_since(cutoff)
    assert len(new_jobs) == 0


# ─── mark_inactive ───────────────────────────────────────────────────────


def test_mark_inactive_flags_missing_offers(repo: JobRepository):
    repo.upsert_job(_make_jobbase(external_id="keep"))
    repo.upsert_job(_make_jobbase(external_id="gone"))

    n = repo.mark_inactive(JobSource.NVISO, kept_external_ids={"keep"})
    assert n == 1
    actives = repo.get_recent_jobs(only_active=True)
    assert len(actives) == 1
    assert actives[0].external_id == "keep"


# ─── export_csv ──────────────────────────────────────────────────────────


def test_export_csv_writes_expected_columns(repo: JobRepository, tmp_path: Path):
    job, _ = repo.upsert_job(_make_jobbase())
    assert job.id is not None
    repo.save_score(ScoreResult(score=72, raw_score=72, job_id=job.id, breakdown=[]))

    out = tmp_path / "out.csv"
    n = repo.export_csv(out, min_score=0)
    assert n == 1
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "id,source,external_id" in content.splitlines()[0]
    assert "72" in content


def test_export_csv_filter_min_score(repo: JobRepository, tmp_path: Path):
    j1, _ = repo.upsert_job(_make_jobbase(external_id="hi"))
    j2, _ = repo.upsert_job(_make_jobbase(external_id="lo"))
    assert j1.id and j2.id
    repo.save_score(ScoreResult(score=85, raw_score=85, job_id=j1.id, breakdown=[]))
    repo.save_score(ScoreResult(score=30, raw_score=30, job_id=j2.id, breakdown=[]))

    out = tmp_path / "out.csv"
    n = repo.export_csv(out, min_score=60)
    assert n == 1


# ─── ScrapeRun history ───────────────────────────────────────────────────


def test_save_run_and_count(repo: JobRepository):
    assert repo.count_runs() == 0
    from datetime import datetime, timezone

    repo.save_run(
        ScrapeRun(started_at=datetime.now(timezone.utc), sources_run=["nviso"])
    )
    assert repo.count_runs() == 1


def test_get_latest_run_returns_most_recent(repo: JobRepository):
    from datetime import datetime, timezone

    t1 = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)
    repo.save_run(ScrapeRun(started_at=t1, sources_run=[]))
    repo.save_run(ScrapeRun(started_at=t2, sources_run=[]))
    latest = repo.get_latest_run()
    assert latest is not None
    # SQLite SQLModel renvoie des datetimes naive — comparaison sur date/heure brute
    assert latest.started_at.replace(tzinfo=timezone.utc) == t2


def test_get_previous_run_returns_second_latest(repo: JobRepository):
    from datetime import datetime, timezone

    t1 = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)
    repo.save_run(ScrapeRun(started_at=t1, sources_run=[]))
    repo.save_run(ScrapeRun(started_at=t2, sources_run=[]))
    previous = repo.get_previous_run()
    assert previous is not None
    assert previous.started_at.replace(tzinfo=timezone.utc) == t1


def test_get_previous_run_none_with_single_run(repo: JobRepository):
    from datetime import datetime, timezone

    repo.save_run(
        ScrapeRun(started_at=datetime.now(timezone.utc), sources_run=[])
    )
    assert repo.get_previous_run() is None


def test_get_previous_run_none_when_empty(repo: JobRepository):
    assert repo.get_previous_run() is None
    assert repo.get_latest_run() is None
