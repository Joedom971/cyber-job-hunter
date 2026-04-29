"""Helpers data partages entre les pages du dashboard.

Toutes les fonctions ici sont pures ou n'ont qu'un side-effect lecture DB :
- testables isolement (cf. tests/test_dashboard_data.py)
- compatibles avec st.cache_data (parametres hashables, retour primitive)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from src.config import Profile
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
    """Charge jobs + dernier score via raw SQL pour bypasser la coercion enum SQLAlchemy.

    Pourquoi raw SQL : si un nouveau scraper est ajouté à `JobSource` mais que
    le process Python en cours (ex: Streamlit) a importé l'enum AVANT, la
    deserialization SQLAlchemy crash sur les valeurs inconnues. Le raw SQL
    récupère les valeurs en str et notre code les normalise via `_enum_str`.
    """
    job_sql = text("""
        SELECT id, source, external_id, title, company, location, country,
               description, url, content_hash, posted_at,
               first_seen_at, last_seen_at, scraped_at, is_active,
               raw_data
        FROM job
    """)
    score_sql = text("""
        SELECT id, job_id, score, raw_score, is_rejected,
               rejection_reasons, matched_keywords, breakdown, computed_at
        FROM scoreresult
    """)

    with repo.session() as session:
        job_rows = session.exec(job_sql).all()  # type: ignore[arg-type]
        score_rows = session.exec(score_sql).all()  # type: ignore[arg-type]

    # Pré-calcule le dernier score par job_id (par computed_at desc)
    latest_score_by_job: dict[int, dict[str, Any]] = {}
    for sr in score_rows:
        sr_dict = dict(sr._mapping)  # type: ignore[attr-defined]
        existing = latest_score_by_job.get(sr_dict["job_id"])
        if existing is None or sr_dict["computed_at"] > existing["computed_at"]:
            latest_score_by_job[sr_dict["job_id"]] = sr_dict

    rows: list[JobRow] = []
    for jr in job_rows:
        j = dict(jr._mapping)  # type: ignore[attr-defined]
        if j.get("id") is None:
            continue
        sr_d = latest_score_by_job.get(j["id"])

        rejection_reasons_raw = _coerce_list(sr_d.get("rejection_reasons") if sr_d else [])
        matched_keywords_raw = _coerce_list(sr_d.get("matched_keywords") if sr_d else [])
        breakdown_raw = _coerce_list(sr_d.get("breakdown") if sr_d else [])
        raw_data_raw = _coerce_dict(j.get("raw_data") or {})

        rows.append(
            JobRow(
                id=j["id"],
                source=_enum_str(j.get("source")),
                company=j.get("company") or "",
                title=j.get("title") or "",
                location=j.get("location") or "",
                country=_enum_str(j.get("country")),
                url=j.get("url") or "",
                description=j.get("description") or "",
                score=int(sr_d["score"]) if sr_d else 0,
                is_rejected=bool(sr_d["is_rejected"]) if sr_d else False,
                is_active=bool(j.get("is_active", True)),
                rejection_reasons=[_enum_str(r) for r in rejection_reasons_raw],
                matched_keywords=list(matched_keywords_raw),
                breakdown=list(breakdown_raw),
                raw_data=raw_data_raw,
                first_seen_at=_coerce_datetime(j.get("first_seen_at")),
                last_seen_at=_coerce_datetime(j.get("last_seen_at")),
                scraped_at=_coerce_datetime(j.get("scraped_at")),
                posted_at=_coerce_datetime(j.get("posted_at"), default=None),
            )
        )
    return rows


def _coerce_list(value):  # type: ignore[no-untyped-def]
    """SQLite stocke les JSON en str ; on désérialise si besoin."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return []


def _coerce_dict(value):  # type: ignore[no-untyped-def]
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def _coerce_datetime(value, default=None):  # type: ignore[no-untyped-def]
    if value is None:
        return default
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return default
    return default


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
    seen_at = row.first_seen_at
    if seen_at.tzinfo is None:
        seen_at = seen_at.replace(tzinfo=timezone.utc)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return seen_at >= since


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_new_offers_cutoff(repo: JobRepository) -> datetime | None:
    """Datetime à partir duquel une offre est considérée "nouvelle" dans le dashboard.

    Logique : on prend le `started_at` de l'avant-dernier run.
    Si moins de 2 runs en DB → None (toutes les offres sont "nouvelles").
    """
    previous = repo.get_previous_run()
    if previous is None:
        return None
    started = previous.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started


def filter_new_only(rows: list[JobRow], since: datetime | None) -> list[JobRow]:
    """Garde uniquement les offres `first_seen_at >= since`. None = pas de filtre."""
    if since is None:
        return rows
    return [r for r in rows if is_new_since(r, since)]
