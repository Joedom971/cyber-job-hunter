"""Logique de déduplication isolée du repository pour testabilité.

Une offre est identifiée par `(source, external_id)`. La détection de
modification se fait via `content_hash` (SHA-256 de title|company|location|description).
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.models import Job, JobBase


def has_content_changed(existing: Job, incoming: JobBase) -> bool:
    """True si le contenu de l'offre a changé depuis le dernier scrape.

    Le hash entrant est recalculé pour pas dépendre de ce que le scraper
    aurait pu mettre dans `incoming.content_hash` (sécurité défensive).
    """
    incoming_hash = Job.compute_content_hash(
        incoming.title, incoming.company, incoming.location, incoming.description
    )
    return incoming_hash != existing.content_hash


def merge_incoming(existing: Job, incoming: JobBase) -> Job:
    """Met à jour `existing` avec les champs de `incoming`.

    Préserve :
        - `id` (PK SQLite)
        - `first_seen_at` (date de découverte initiale)

    Met à jour :
        - tous les champs de contenu
        - `content_hash` recalculé
        - `last_seen_at` et `scraped_at` à maintenant
        - `is_active = True` (l'offre est de nouveau visible côté source)
    """
    now = datetime.now(timezone.utc)
    existing.title = incoming.title
    existing.company = incoming.company
    existing.location = incoming.location
    existing.country = incoming.country
    existing.description = incoming.description
    existing.url = incoming.url
    existing.posted_at = incoming.posted_at
    existing.language_hints = list(incoming.language_hints)
    existing.raw_data = dict(incoming.raw_data)
    existing.content_hash = Job.compute_content_hash(
        incoming.title, incoming.company, incoming.location, incoming.description
    )
    existing.last_seen_at = now
    existing.scraped_at = now
    existing.is_active = True
    return existing
