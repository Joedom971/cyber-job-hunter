"""Helpers data partages entre les pages du dashboard.

Toutes les fonctions ici sont pures ou n'ont qu'un side-effect lecture DB :
- testables isolement (cf. tests/test_dashboard_data.py)
- compatibles avec st.cache_data (parametres hashables, retour primitive)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from src.config import Profile
from src.models import Job, ScoreResult
from src.storage import JobRepository


@dataclass(frozen=True)
class JobRow:
    id: int
    source: str
    company: str
    title: str
    location: str
    country: str
    url: str
    description: str
    score: int
    is_rejected: bool
    is_active: bool
    rejection_reasons: list[str]
    matched_keywords: list[str]
    breakdown: list[dict]
    raw_data: dict
    first_seen_at: datetime
    last_seen_at: datetime
    scraped_at: datetime
    posted_at: datetime | None


def open_repo(db_path: Path | None = None) -> JobRepository:
    db_url = f"sqlite:///{db_path}" if db_path else None
    return JobRepository(db_url=db_url)


def _run(session: Session, statement):  # type: ignore[no-untyped-def]
    return session.exec(statement)


def _enum_str(value) -> str:  # type: ignore[no-untyped-def]
    """SQLModel renvoie parfois un str brut au lieu de l'enum après lecture DB.

    Ce helper accepte les deux et retourne toujours un str.
    """
    return value.value if hasattr(value, "value") else str(value)


def load_all_jobs_with_latest_score(repo: JobRepository) -> list[JobRow]:
    with repo.session() as session:
        jobs = list(_run(session, select(Job)).all())
        all_scores = list(_run(session, select(ScoreResult)).all())

    scores_by_job: dict[int, ScoreResult] = {}
    for sr in all_scores:
        current = scores_by_job.get(sr.job_id)
        if current is None or sr.computed_at > current.computed_at:
            scores_by_job[sr.job_id] = sr

    rows: list[JobRow] = []
    for job in jobs:
        if job.id is None:
            continue
        sr = scores_by_job.get(job.id)
        rows.append(
            JobRow(
                id=job.id,
                source=_enum_str(job.source),
                company=job.company,
                title=job.title,
                location=job.location or "",
                country=_enum_str(job.country),
                url=job.url,
                description=job.description or "",
                score=sr.score if sr else 0,
                is_rejected=sr.is_rejected if sr else False,
                is_active=job.is_active,
                rejection_reasons=[
                    _enum_str(r) for r in (sr.rejection_reasons if sr else [])
                ],
                matched_keywords=list(sr.matched_keywords) if sr else [],
                breakdown=list(sr.breakdown) if sr else [],
                raw_data=dict(job.raw_data) if job.raw_data else {},
                first_seen_at=job.first_seen_at,
                last_seen_at=job.last_seen_at,
                scraped_at=job.scraped_at,
                posted_at=job.posted_at,
            )
        )
    return rows


def filter_rows(
    rows: list[JobRow],
    *,
    min_score: int = 0,
    max_score: int = 100,
    sources: set[str] | None = None,
    countries: set[str] | None = None,
    only_active: bool = True,
    hide_rejected: bool = True,
    search_text: str = "",
    discovered_within_days: int | None = None,
    matched_keywords_any: set[str] | None = None,
    keyword_categories_any: set[str] | None = None,
    profile: Profile | None = None,
) -> list[JobRow]:
    """Applique les filtres UI sur une liste de JobRow.

    Args clés :
        min_score / max_score    : plage de score [0, 100]
        discovered_within_days   : ne garde que les offres découvertes < N jours
        matched_keywords_any     : au moins UN de ces keywords doit être matché
        keyword_categories_any   : au moins UN keyword d'une de ces catégories
                                   (nécessite `profile` pour la résolution)
    """
    cutoff: datetime | None = None
    if discovered_within_days is not None and discovered_within_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=discovered_within_days)

    # Pré-calcule l'union des keywords pour chaque catégorie demandée
    category_keywords: set[str] = set()
    if keyword_categories_any and profile is not None:
        for cat in keyword_categories_any:
            category_keywords.update(
                k.lower() for k in getattr(profile.technical_keywords, cat, [])
            )

    needle = search_text.strip().lower()
    out: list[JobRow] = []
    for r in rows:
        if only_active and not r.is_active:
            continue
        if hide_rejected and r.is_rejected:
            continue
        if r.score < min_score or r.score > max_score:
            continue
        if sources and r.source not in sources:
            continue
        if countries and r.country not in countries:
            continue
        if needle and needle not in (r.title + " " + r.company).lower():
            continue
        if cutoff is not None:
            seen_at = r.first_seen_at if r.first_seen_at.tzinfo else r.first_seen_at.replace(tzinfo=timezone.utc)
            if seen_at < cutoff:
                continue
        if matched_keywords_any:
            row_kw = {k.lower() for k in r.matched_keywords}
            wanted = {k.lower() for k in matched_keywords_any}
            if not row_kw & wanted:
                continue
        if category_keywords:
            row_kw = {k.lower() for k in r.matched_keywords}
            if not row_kw & category_keywords:
                continue
        out.append(r)
    return out


def collect_all_matched_keywords(rows: list[JobRow]) -> list[str]:
    """Liste triée et dédupliquée des keywords présents en DB. Sert au multi-select."""
    seen: set[str] = set()
    for r in rows:
        for kw in r.matched_keywords:
            seen.add(kw)
    return sorted(seen)


KEYWORD_CATEGORIES: tuple[str, ...] = (
    "defensive",
    "offensive",
    "scripting",
    "systems",
    "cloud",
    "grc",
    "siem_tools",
)


def sort_rows(rows: list[JobRow], by: str = "score") -> list[JobRow]:
    if by == "score":
        return sorted(rows, key=lambda r: (-r.score, -r.scraped_at.timestamp()))
    if by == "recent":
        return sorted(rows, key=lambda r: -r.scraped_at.timestamp())
    if by == "first_seen":
        return sorted(rows, key=lambda r: -r.first_seen_at.timestamp())
    return rows


@dataclass(frozen=True)
class GlobalStats:
    total_jobs: int
    active_jobs: int
    scored_jobs: int
    rejected_jobs: int
    top_score: int
    avg_score: float
    sources_count: dict[str, int]


def compute_stats(rows: list[JobRow]) -> GlobalStats:
    active = [r for r in rows if r.is_active]
    scored = [r for r in active if not r.is_rejected]
    rejected = [r for r in active if r.is_rejected]
    sources_count: dict[str, int] = {}
    for r in active:
        sources_count[r.source] = sources_count.get(r.source, 0) + 1

    top = max((r.score for r in scored), default=0)
    avg = sum(r.score for r in scored) / len(scored) if scored else 0.0

    return GlobalStats(
        total_jobs=len(rows),
        active_jobs=len(active),
        scored_jobs=len(scored),
        rejected_jobs=len(rejected),
        top_score=top,
        avg_score=avg,
        sources_count=sources_count,
    )


def is_new_since(row: JobRow, since: datetime) -> bool:
    return row.first_seen_at >= since


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
