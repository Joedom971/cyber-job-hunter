"""Couche de persistance SQLite via SQLModel.

Usage typique :
    >>> repo = JobRepository()  # data/jobs.db par defaut
    >>> repo.create_all()
    >>> job, is_new = repo.upsert_job(job_from_scraper)
    >>> repo.save_score(score_result_for_that_job)
    >>> for j in repo.get_recent_jobs(since_hours=24, min_score=60): ...
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_
from sqlmodel import Session, SQLModel, create_engine, select

from src.deduplication import has_content_changed, merge_incoming
from src.models import Job, JobBase, JobSource, ScoreResult

DEFAULT_DB_PATH: Path = Path("data/jobs.db")


def _run(session: Session, statement):  # type: ignore[no-untyped-def]
    """Petit wrapper sur session.exec pour centraliser les requetes typees."""
    return session.exec(statement)


class JobRepository:
    """Wrapper sur l'engine SQLModel. Encapsule toutes les requetes du projet."""

    def __init__(self, db_url: str | None = None) -> None:
        if db_url is None:
            DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            db_url = f"sqlite:///{DEFAULT_DB_PATH}"
        self.db_url = db_url
        self.engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            echo=False,
        )

    def create_all(self) -> None:
        SQLModel.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        SQLModel.metadata.drop_all(self.engine)

    def session(self) -> Session:
        return Session(self.engine)

    def upsert_job(self, incoming: JobBase) -> tuple[Job, bool]:
        now = datetime.now(timezone.utc)

        with self.session() as session:
            stmt = select(Job).where(
                and_(Job.source == incoming.source, Job.external_id == incoming.external_id)
            )
            existing = _run(session, stmt).first()

            if existing is None:
                job = Job.model_validate(
                    incoming.model_dump(),
                    update={
                        "content_hash": Job.compute_content_hash(
                            incoming.title,
                            incoming.company,
                            incoming.location,
                            incoming.description,
                        ),
                        "first_seen_at": now,
                        "last_seen_at": now,
                        "scraped_at": now,
                        "is_active": True,
                    },
                )
                session.add(job)
                session.commit()
                session.refresh(job)
                return job, True

            if has_content_changed(existing, incoming):
                merge_incoming(existing, incoming)
            else:
                existing.last_seen_at = now
                existing.scraped_at = now
                existing.is_active = True
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing, False

    def save_score(self, score: ScoreResult) -> ScoreResult:
        with self.session() as session:
            session.add(score)
            session.commit()
            session.refresh(score)
            return score

    def get_latest_score(self, job_id: int) -> ScoreResult | None:
        with self.session() as session:
            stmt = (
                select(ScoreResult)
                .where(ScoreResult.job_id == job_id)
                .order_by(ScoreResult.computed_at.desc())  # type: ignore[union-attr]
                .limit(1)
            )
            return _run(session, stmt).first()

    def get_recent_jobs(
        self, since_hours: int = 24, min_score: int = 0, only_active: bool = True
    ) -> list[Job]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        with self.session() as session:
            stmt = select(Job).where(Job.scraped_at >= cutoff)  # type: ignore[arg-type]
            if only_active:
                stmt = stmt.where(Job.is_active.is_(True))  # type: ignore[union-attr]
            jobs = list(_run(session, stmt).all())

            if min_score <= 0:
                return jobs

            kept: list[Job] = []
            for job in jobs:
                if job.id is None:
                    continue
                latest = self.get_latest_score(job.id)
                if latest is not None and latest.score >= min_score:
                    kept.append(job)
            return kept

    def get_new_jobs_since(self, since: datetime) -> list[Job]:
        with self.session() as session:
            stmt = select(Job).where(Job.first_seen_at >= since)  # type: ignore[arg-type]
            return list(_run(session, stmt).all())

    def count_jobs(self) -> int:
        with self.session() as session:
            return len(list(_run(session, select(Job)).all()))

    def mark_inactive(self, source: JobSource, kept_external_ids: set[str]) -> int:
        with self.session() as session:
            stmt = select(Job).where(
                and_(Job.source == source, Job.is_active.is_(True))  # type: ignore[union-attr]
            )
            jobs = list(_run(session, stmt).all())
            count = 0
            for job in jobs:
                if job.external_id not in kept_external_ids:
                    job.is_active = False
                    session.add(job)
                    count += 1
            session.commit()
            return count

    def export_csv(self, output_path: Path, min_score: int = 0) -> int:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fields = (
            "id", "source", "external_id", "title", "company", "location",
            "country", "url", "score", "is_active", "posted_at",
            "first_seen_at", "scraped_at",
        )

        with self.session() as session:
            stmt = select(Job).where(Job.is_active.is_(True))  # type: ignore[union-attr]
            jobs = list(_run(session, stmt).all())

        rows_written = 0
        with output_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for job in jobs:
                if job.id is None:
                    continue
                latest = self.get_latest_score(job.id)
                score = latest.score if latest else 0
                if score < min_score:
                    continue
                writer.writerow(
                    {
                        "id": job.id,
                        "source": job.source.value,
                        "external_id": job.external_id,
                        "title": job.title,
                        "company": job.company,
                        "location": job.location or "",
                        "country": job.country.value,
                        "url": job.url,
                        "score": score,
                        "is_active": job.is_active,
                        "posted_at": job.posted_at.isoformat() if job.posted_at else "",
                        "first_seen_at": job.first_seen_at.isoformat(),
                        "scraped_at": job.scraped_at.isoformat(),
                    }
                )
                rows_written += 1
        return rows_written
