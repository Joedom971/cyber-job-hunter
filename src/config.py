"""Chargement et validation typée des configurations YAML + .env.

Pourquoi typer la config :
    Une typo dans `profile.yaml` (ex: `targetd_titles`) déclencherait Pydantic
    à parser → erreur explicite au démarrage plutôt que silencieusement passer
    à côté d'une règle de scoring.

Cache :
    Les configs sont chargées une fois par process via `functools.lru_cache`.
    Pour invalider en test : `load_profile.cache_clear()`.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Racine du repo (config/, src/, ...)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_PATH: Path = PROJECT_ROOT / "config" / "profile.yaml"
DEFAULT_SOURCES_PATH: Path = PROJECT_ROOT / "config" / "sources.yaml"


# ─── Profile ─────────────────────────────────────────────────────────────


class TechnicalKeywords(BaseModel):
    """Mots-clés techniques par catégorie. Les listes sont fusionnées au scoring."""

    model_config = ConfigDict(extra="forbid")

    defensive: list[str] = Field(default_factory=list)
    offensive: list[str] = Field(default_factory=list)
    scripting: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    cloud: list[str] = Field(default_factory=list)
    grc: list[str] = Field(default_factory=list)
    siem_tools: list[str] = Field(default_factory=list)

    @property
    def all_flat(self) -> list[str]:
        return [
            kw
            for category in (
                self.defensive,
                self.offensive,
                self.scripting,
                self.systems,
                self.cloud,
                self.grc,
                self.siem_tools,
            )
            for kw in category
        ]


class Locations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred: list[str] = Field(default_factory=list)
    good: list[str] = Field(default_factory=list)
    flanders_only_if_english: list[str] = Field(default_factory=list)


class Languages(BaseModel):
    model_config = ConfigDict(extra="forbid")

    french_english_bonus: int = 10
    english_only_bonus: int = 8
    french_only_bonus: int = 8
    dutch_nice_to_have_bonus: int = 5


class Experience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reject_patterns: list[str] = Field(default_factory=list)
    penalty_3y: int = -10
    penalty_3y_patterns: list[str] = Field(default_factory=list)


class Education(BaseModel):
    model_config = ConfigDict(extra="forbid")

    master_mandatory_penalty: int = -20
    bachelor_required_penalty: int = -5
    master_mandatory_patterns: list[str] = Field(default_factory=list)
    bachelor_required_patterns: list[str] = Field(default_factory=list)
    master_with_alternative_patterns: list[str] = Field(default_factory=list)


class ScoreBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: int = 0
    max: int = 100
    technical_keywords_cap: int = 30


class Profile(BaseModel):
    """Profil complet, chargé depuis `config/profile.yaml`."""

    model_config = ConfigDict(extra="forbid")

    target_titles: list[str]
    junior_keywords: list[str] = Field(default_factory=list)
    graduate_keywords: list[str] = Field(default_factory=list)
    technical_keywords: TechnicalKeywords
    locations: Locations
    languages: Languages
    dutch_required_patterns: list[str] = Field(default_factory=list)
    language_alternative_patterns: list[str] = Field(default_factory=list)
    experience: Experience
    education: Education
    score_bounds: ScoreBounds = Field(default_factory=ScoreBounds)


# ─── Sources ─────────────────────────────────────────────────────────────


class SourceType(StrEnum):
    REST_API = "rest_api"
    RECRUITEE = "recruitee"
    HTML = "html"
    HTML_CLOUDFLARE = "html_cloudflare"
    SPA_REACT = "spa_react"
    WORKDAY = "workday"
    AVATURE = "avature"
    RSS = "rss"


class SourceConfig(BaseModel):
    """Config d'une source individuelle. Hérite des defaults au chargement."""

    model_config = ConfigDict(extra="allow")  # tolère champs futurs (notes, sprint, ...)

    enabled: bool = False
    type: SourceType
    base_url: str
    rate_limit_seconds: float
    jitter_max_seconds: float
    max_pages: int
    timeout_seconds: float
    max_retries: int
    backoff_base_seconds: float
    user_agent: str
    respect_robots_txt: bool
    min_hours_between_runs: int
    company_name_override: str | None = None
    country_default: str | None = None


class SourcesDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_limit_seconds: float = 3.0
    jitter_max_seconds: float = 1.5
    max_pages: int = 5
    timeout_seconds: float = 15.0
    max_retries: int = 3
    backoff_base_seconds: float = 5.0
    user_agent: str
    respect_robots_txt: bool = True
    min_hours_between_runs: int = 12


class LinkedInConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    rate_limit_seconds: float = 3.0
    max_jobs_per_day: int = 200
    abort_on_bot_detection: bool = True
    notes: str = ""


class SourcesConfig(BaseModel):
    """Configuration agrégée des sources Sprint 1 + roadmap."""

    model_config = ConfigDict(extra="forbid")

    defaults: SourcesDefaults
    sources: dict[str, SourceConfig]
    planned_sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    linkedin: LinkedInConfig = Field(default_factory=LinkedInConfig)

    @field_validator("sources")
    @classmethod
    def _at_least_one_enabled(cls, v: dict[str, SourceConfig]) -> dict[str, SourceConfig]:
        if not any(s.enabled for s in v.values()):
            # Warning non-bloquant : possible en dev quand on désactive tout
            # (le runner loggera et exit 0).
            pass
        return v

    @property
    def enabled_sources(self) -> dict[str, SourceConfig]:
        return {k: v for k, v in self.sources.items() if v.enabled}


# ─── Settings (.env) ─────────────────────────────────────────────────────


class Settings(BaseModel):
    """Variables d'environnement. Charge `.env` automatiquement.

    Tous les champs sont optionnels — l'absence d'un credential désactive
    proprement la feature concernée plutôt que de crasher.
    """

    model_config = ConfigDict(extra="ignore")

    # Email digest (Sprint 3)
    gmail_user: str | None = None
    gmail_app_password: str | None = None
    digest_recipient: str | None = None
    digest_min_score: int = 60
    digest_max_jobs: int = 10
    digest_dashboard_url: str = "http://localhost:8501"

    # Scraping
    scraper_user_agent: str | None = None
    scraper_rate_limit_seconds: float | None = None

    # Logging
    log_level: str = "INFO"

    @property
    def email_digest_enabled(self) -> bool:
        return all(
            v is not None and v != ""
            for v in (self.gmail_user, self.gmail_app_password, self.digest_recipient)
        )


# ─── Loaders ─────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config introuvable : {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Config {path} doit être un mapping YAML, reçu {type(data).__name__}")
    return data


@lru_cache(maxsize=1)
def load_profile(path: Path | None = None) -> Profile:
    return Profile.model_validate(_load_yaml(path or DEFAULT_PROFILE_PATH))


@lru_cache(maxsize=1)
def load_sources(path: Path | None = None) -> SourcesConfig:
    raw = _load_yaml(path or DEFAULT_SOURCES_PATH)
    defaults_dict = raw.get("defaults", {})

    # Fusionne defaults dans chaque source individuelle
    merged_sources: dict[str, dict[str, Any]] = {}
    for name, src in (raw.get("sources") or {}).items():
        merged_sources[name] = {**defaults_dict, **src}

    return SourcesConfig.model_validate(
        {
            "defaults": defaults_dict,
            "sources": merged_sources,
            "planned_sources": raw.get("planned_sources") or {},
            "linkedin": raw.get("linkedin") or {},
        }
    )


@lru_cache(maxsize=1)
def load_settings(env_path: Path | None = None) -> Settings:
    """Charge `.env` puis instancie `Settings` depuis `os.environ`."""
    load_dotenv(env_path or PROJECT_ROOT / ".env", override=False)
    return Settings(
        gmail_user=os.environ.get("GMAIL_USER"),
        gmail_app_password=os.environ.get("GMAIL_APP_PASSWORD"),
        digest_recipient=os.environ.get("DIGEST_RECIPIENT"),
        digest_min_score=int(os.environ.get("DIGEST_MIN_SCORE", "60")),
        digest_max_jobs=int(os.environ.get("DIGEST_MAX_JOBS", "10")),
        digest_dashboard_url=os.environ.get(
            "DIGEST_DASHBOARD_URL", "http://localhost:8501"
        ),
        scraper_user_agent=os.environ.get("SCRAPER_USER_AGENT"),
        scraper_rate_limit_seconds=(
            float(v) if (v := os.environ.get("SCRAPER_RATE_LIMIT_SECONDS")) else None
        ),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )


def reset_caches() -> None:
    """Invalide les caches de chargement. Utile pour les tests."""
    load_profile.cache_clear()
    load_sources.cache_clear()
    load_settings.cache_clear()
