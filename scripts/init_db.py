"""Initialise / réinitialise la base SQLite du projet.

Usage :
    python scripts/init_db.py            # crée data/jobs.db si absente
    python scripts/init_db.py --reset    # drop + recreate (DEMANDE confirmation)
    python scripts/init_db.py --db sqlite:///tmp.db
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permet de lancer le script sans installer le package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click
from loguru import logger

from src.storage import JobRepository


@click.command()
@click.option("--db", "db_url", default=None, help="URL SQLAlchemy (override).")
@click.option(
    "--reset",
    is_flag=True,
    help="Drop toutes les tables avant de les recréer (DESTRUCTIF).",
)
@click.option("--yes", is_flag=True, help="Skip la confirmation de --reset.")
def main(db_url: str | None, reset: bool, yes: bool) -> None:
    repo = JobRepository(db_url=db_url)
    logger.info("Engine configuré sur : {}", repo.db_url)

    if reset:
        if not yes and not click.confirm(
            "⚠️  --reset va DROP toutes les tables. Continuer ?", default=False
        ):
            logger.warning("Annulé par l'utilisateur.")
            sys.exit(0)
        logger.warning("Drop des tables existantes...")
        repo.drop_all()

    logger.info("Création des tables (idempotent)...")
    repo.create_all()
    logger.success("Base prête. Jobs actuellement en DB : {}", repo.count_jobs())


if __name__ == "__main__":
    main()
