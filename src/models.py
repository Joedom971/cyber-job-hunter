"""Modèles de données du scraper.

Pattern :
- `*Base` (Pydantic / SQLModel sans table) → validation, sérialisation, transport
- `*` (SQLModel, table=True)               → persistence SQLite

Ce découpage permet d'utiliser les `*Base` librement dans les tests, les
scrapers et les API sans dépendre de la couche DB.
"""

from __future__ import annotations

import hashlib
from datetime import date as Date
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field as PydField, HttpUrl, field_validator
from sqlmodel import JSON, Column, Field, SQLModel, UniqueConstraint


# ─── Enums ───────────────────────────────────────────────────────────────


class JobSource(StrEnum):
    """Source d'origine d'une offre. Utilisé pour le routing scraper et le tri."""

    REMOTIVE = "remotive"
    NVISO = "nviso"
    ITSME = "itsme"
    EASI = "easi"
    SMALS = "smals"
    CREAM = "cream"
    # Étendu en Sprint 3+
    OTHER = "other"


class Country(StrEnum):
    BE = "BE"
    LU = "LU"
    FR = "FR"
    NL = "NL"
    DE = "DE"
    IE = "IE"
    REMOTE = "REMOTE"
    OTHER = "OTHER"


class Language(StrEnum):
    """Langues détectées dans une offre. Utile pour scoring + filters."""

    FR = "fr"
    EN = "en"
    NL = "nl"
    DE = "de"
    OTHER = "other"


class RejectReason(StrEnum):
    """Raisons de rejet (filters.py). Affichées dans le digest stats."""

    SENIOR_REQUIRED = "senior_required"
    EXPERIENCE_5Y = "experience_5y"
    MASTER_MANDATORY = "master_mandatory"
    DUTCH_REQUIRED = "dutch_required"
    LOCATION_OUT_OF_SCOPE = "location_out_of_scope"
    OTHER = "other"


# ─── Job ─────────────────────────────────────────────────────────────────


class JobBase(SQLModel):
    """Forme normalisée d'une offre, indépendante de la source.

    Tout scraper doit produire un `JobBase` (ou `Job`) avec ces champs.
    Les champs marqués Optional peuvent être `None` si la source ne les fournit pas.
    """

    source: JobSource
    external_id: str = Field(
        description="Identifiant unique côté source (ex. ID Recruitee, slug Remotive)"
    )
    title: str = Field(min_length=1, max_length=500)
    company: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    country: Country = Country.OTHER
    description: str = Field(default="", description="Texte brut de l'offre, peut contenir du HTML")
    url: str = Field(min_length=1, max_length=1000)
    posted_at: datetime | None = None
    language_hints: list[Language] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="Langues détectées dans le titre / la description",
    )
    raw_data: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="Payload brut renvoyé par la source, utile au debug",
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        # On utilise HttpUrl uniquement pour valider, puis on stocke en str
        # (sinon SQLModel galère avec le type personnalisé Pydantic)
        HttpUrl(v)
        return v


class Job(JobBase, table=True):
    """Offre persistée. Clé d'unicité = (source, external_id).

    `content_hash` permet de détecter une mise à jour (titre/desc) sur la même offre.
    `first_seen_at` reste figé au premier scraping → utilisé pour le badge "Nouveau".
    `last_seen_at` est mis à jour à chaque run où l'offre réapparaît.
    """

    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external"),)

    id: int | None = Field(default=None, primary_key=True)
    content_hash: str = Field(
        index=True,
        max_length=64,
        description="SHA-256 hex de title|company|location|description (normalisé)",
    )
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    is_active: bool = Field(default=True, description="False si l'offre a disparu de la source")

    # Note: relation `scores` (1-N → ScoreResult) sera ajoutée dans `src/storage.py`
    # quand on aura besoin de la traverser (Sprint 1 / étape 6). On évite ici la
    # syntaxe `Relationship` qui demande des annotations Mapped[] complexes
    # incompatibles avec SQLModel < 0.1 + SQLAlchemy 2.x stricts.

    @staticmethod
    def compute_content_hash(
        title: str, company: str, location: str | None, description: str
    ) -> str:
        """Hash stable utilisé pour la déduplication et la détection de modifications."""
        parts = (title, company, location or "", description)
        normalized = "|".join((p or "").strip().lower() for p in parts)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ─── Scoring ─────────────────────────────────────────────────────────────


class ScoreComponent(BaseModel):
    """Un élément individuel du scoring, pour la transparence ("explain")."""

    model_config = ConfigDict(frozen=True)

    rule: str = PydField(description="Identifiant lisible de la règle (ex. 'title_match_soc')")
    points: int = PydField(description="Points ajoutés (peut être négatif)")
    detail: str = PydField(default="", description="Contexte humain : mot-clé matché, etc.")


class ScoreResultBase(SQLModel):
    """Résultat de scoring d'une offre. Reproductible à partir de `Job` + profil."""

    score: int = Field(ge=0, le=100, description="Score final clampé [0, 100]")
    raw_score: int = Field(description="Score avant clamp (peut être négatif ou > 100)")
    is_rejected: bool = Field(default=False, description="True si une règle de rejet a matché")
    rejection_reasons: list[RejectReason] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    matched_keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    breakdown: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="Liste sérialisée de ScoreComponent (pour explain dans le dashboard)",
    )


class ScoreResult(ScoreResultBase, table=True):
    """Résultat persisté. Un job peut avoir plusieurs scores (historique)."""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    computed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True
    )


# ─── Digest / stats (Sprint 3) ───────────────────────────────────────────


class DigestStats(BaseModel):
    """Statistiques d'un run de scraping, utilisées par le digest email."""

    model_config = ConfigDict(frozen=True)

    date: Date
    total_scanned: int = PydField(ge=0)
    kept_count: int = PydField(ge=0)
    rejected_count: int = PydField(ge=0)
    new_count: int = PydField(ge=0, description="Offres jamais vues précédemment")
    rejected_by_reason: dict[str, int] = PydField(default_factory=dict)

    @property
    def rejection_rate(self) -> float:
        return self.rejected_count / self.total_scanned if self.total_scanned else 0.0
