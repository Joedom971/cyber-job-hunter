"""Export CSV des offres actives en DB.

Usage :
    python scripts/export_csv.py                          # data/exports/jobs_YYYYMMDD.csv
    python scripts/export_csv.py --min-score 60
    python scripts/export_csv.py --output /tmp/out.csv
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click
from loguru import logger

from src.storage import JobRepository


def _default_output_path() -> Path:
    today_iso = date.today().isoformat().replace("-", "")
    return Path("data/exports") / f"jobs_{today_iso}.csv"


@click.command()
@click.option("--db", "db_url", default=None, help="URL SQLAlchemy override.")
@click.option("--min-score", default=0, type=int, help="Filtre minimum de score [0-100].")
@click.option(
    "--output", "output_path", default=None, type=click.Path(),
    help="Chemin de sortie. Default : data/exports/jobs_YYYYMMDD.csv",
)
def main(db_url: str | None, min_score: int, output_path: str | None) -> None:
    repo = JobRepository(db_url=db_url)
    out = Path(output_path) if output_path else _default_output_path()
    n = repo.export_csv(out, min_score=min_score)
    logger.success("Exporté {} offres → {}", n, out)
    if n == 0:
        logger.warning(
            "Aucune offre exportée. Vérifie que --min-score n'est pas trop élevé "
            "et que `run_scrape.py` a été lancé au moins une fois."
        )


if __name__ == "__main__":
    main()
