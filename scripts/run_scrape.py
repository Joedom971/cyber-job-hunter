"""Orchestrateur principal : lance tous les scrapers actifs et calcule les scores.

Usage :
    python scripts/run_scrape.py                       # tous les scrapers actifs
    python scripts/run_scrape.py --source remotive     # 1 seul
    python scripts/run_scrape.py --source nviso,easi   # plusieurs (CSV)
    python scripts/run_scrape.py --no-score            # skip scoring (debug parsing)
    python scripts/run_scrape.py --dry-run             # ne persiste rien en DB
    python scripts/run_scrape.py --db sqlite:///alt.db # override de la DB

Sortie console :
    - 1 ligne par source : fetched / new / updated / inactive / scored / rejected / avg
    - Top 10 des offres scorées du run (toutes sources confondues)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Permet de lancer le script sans installer le package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click
from loguru import logger

from src.config import load_profile, load_sources
from src.scoring import score_job
from src.scrapers import SCRAPER_FACTORIES
from src.storage import JobRepository


@click.command()
@click.option("--source", "sources_filter", default=None,
              help="Sources à scraper (CSV). Default: toutes les enabled.")
@click.option("--db", "db_url", default=None, help="URL SQLAlchemy override.")
@click.option("--no-score", is_flag=True, help="Skip le scoring (debug parsing).")
@click.option("--dry-run", is_flag=True, help="Ne persiste rien (utile pour tester).")
def main(sources_filter: str | None, db_url: str | None, no_score: bool, dry_run: bool) -> None:
    profile = load_profile()
    sources_config = load_sources()
    enabled = sources_config.enabled_sources

    if sources_filter:
        wanted = {s.strip() for s in sources_filter.split(",") if s.strip()}
        sources = {k: v for k, v in enabled.items() if k in wanted}
        unknown = wanted - set(enabled.keys())
        if unknown:
            logger.warning("Sources inconnues ou désactivées : {}", sorted(unknown))
        if not sources:
            logger.error("Aucune source à scraper après filtre.")
            sys.exit(1)
    else:
        sources = enabled

    if not sources:
        logger.warning("Aucune source activée dans sources.yaml. Rien à faire.")
        sys.exit(0)

    repo: JobRepository | None = None
    if not dry_run:
        repo = JobRepository(db_url=db_url)
        repo.create_all()
    else:
        logger.warning("--dry-run : aucune écriture en DB.")

    run_started = datetime.now(timezone.utc)
    summaries: list[dict[str, object]] = []

    for name, src_cfg in sources.items():
        if name not in SCRAPER_FACTORIES:
            logger.warning("[{}] no factory registered, skipping.", name)
            continue

        logger.info("─── {} ───", name)
        factory = SCRAPER_FACTORIES[name]
        with factory(src_cfg, repo=repo) as scraper:
            run_result = scraper.run()

        scored = 0
        rejected = 0
        scores_for_avg: list[int] = []

        if repo is not None and not no_score:
            recent = repo.get_recent_jobs(since_hours=1, only_active=True)
            recent = [j for j in recent if j.source == run_result.source]
            for job in recent:
                if job.id is None:
                    continue
                sr = score_job(job, profile)
                sr.job_id = job.id
                repo.save_score(sr)
                if sr.is_rejected:
                    rejected += 1
                else:
                    scored += 1
                    scores_for_avg.append(sr.score)

        summaries.append(
            {
                "name": name,
                "fetched": run_result.jobs_fetched,
                "inserted": run_result.jobs_inserted,
                "updated": run_result.jobs_updated,
                "inactive": run_result.jobs_marked_inactive,
                "scored": scored,
                "rejected": rejected,
                "avg": (sum(scores_for_avg) / len(scores_for_avg)) if scores_for_avg else 0.0,
                "errors": len(run_result.errors),
                "aborted": run_result.aborted_reason,
            }
        )

    _print_summary(summaries)
    if repo is not None:
        _print_top_jobs(repo, since=run_started, limit=10)


def _print_summary(summaries: list[dict[str, object]]) -> None:
    click.echo("")
    click.echo("═══ SUMMARY ═══")
    if not summaries:
        click.echo("(aucune source exécutée)")
        return
    header = (
        f"  {'source':<10} {'fetched':>7} {'new':>4} {'upd':>4} {'inactive':>8} "
        f"{'scored':>6} {'rejected':>8} {'avg':>5}  {'errors':>6}  status"
    )
    click.echo(header)
    click.echo("  " + "─" * (len(header) - 2))
    for s in summaries:
        status = s["aborted"] or "ok"
        click.echo(
            f"  {s['name']:<10} {s['fetched']:>7} {s['inserted']:>4} {s['updated']:>4} "
            f"{s['inactive']:>8} {s['scored']:>6} {s['rejected']:>8} "
            f"{float(s['avg']):>5.1f}  {s['errors']:>6}  {status}"  # type: ignore[arg-type]
        )


def _print_top_jobs(repo: JobRepository, since: datetime, limit: int) -> None:
    new_jobs = repo.get_new_jobs_since(since)
    if not new_jobs:
        return
    scored: list[tuple[int, str, str, str]] = []
    for job in new_jobs:
        if job.id is None:
            continue
        latest = repo.get_latest_score(job.id)
        if latest is None or latest.is_rejected:
            continue
        scored.append((latest.score, job.title, job.company, job.location or "remote"))
    scored.sort(reverse=True)

    if not scored:
        return
    click.echo("")
    click.echo(f"═══ TOP {min(limit, len(scored))} (offres nouvelles ce run) ═══")
    for score, title, company, loc in scored[:limit]:
        click.echo(f"  [{score:>3}] {title[:55]:<55} | {company[:18]:<18} | {loc[:15]}")


if __name__ == "__main__":
    main()
