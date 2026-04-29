"""Smoke tests pour le runner et l'export CSV.

Les tests unitaires des scrapers / scoring / storage ont déjà couvert le détail.
Ici on vérifie l'intégration end-to-end : un appel CLI fait bien ce qu'on attend.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from src.scrapers import SCRAPER_FACTORIES, available_sources, get_factory
from src.scrapers.easi import EasiScraper
from src.scrapers.nviso import NvisoScraper
from src.scrapers.recruitee import RecruiteeScraper
from src.scrapers.remotive import RemotiveScraper


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.scrapers.base.time.sleep", lambda *_a, **_kw: None)


# ─── Registry ────────────────────────────────────────────────────────────


def test_registry_contains_all_active_sources():
    expected = {"remotive", "nviso", "itsme", "easi", "smals", "cream"}
    assert set(SCRAPER_FACTORIES.keys()) == expected


def test_available_sources_sorted():
    assert available_sources() == sorted(SCRAPER_FACTORIES.keys())


def test_factory_returns_correct_class():
    from src.config import SourceConfig, SourceType

    cfg = SourceConfig(
        enabled=True, type=SourceType.REST_API, base_url="https://x.test/api",
        rate_limit_seconds=0.0, jitter_max_seconds=0.0, max_pages=1,
        timeout_seconds=5.0, max_retries=1, backoff_base_seconds=0.01,
        user_agent="UA", respect_robots_txt=False, min_hours_between_runs=0,
    )

    assert isinstance(get_factory("remotive")(cfg), RemotiveScraper)
    assert isinstance(get_factory("nviso")(cfg), NvisoScraper)
    assert isinstance(get_factory("itsme")(cfg), RecruiteeScraper)
    assert isinstance(get_factory("easi")(cfg), EasiScraper)


# ─── Runner CLI (end-to-end) ─────────────────────────────────────────────


@respx.mock
def test_run_scrape_full_e2e(tmp_path: Path):
    """Lance le runner en isolant le filesystem (DB) et en mockant les 4 endpoints."""
    # Mocks pour les 4 sources Sprint 1 (basés sur les vrais payloads)
    respx.get("https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(
            200, json={"jobs": [{
                "id": 1, "url": "https://remotive.com/j/1", "title": "SOC Junior",
                "company_name": "RemoCo", "candidate_required_location": "Worldwide",
                "publication_date": "2026-04-29T10:00:00Z",
                "description": "<p>junior python</p>",
            }]},
        )
    )
    respx.get("https://www.nviso.eu/jobs/").mock(
        return_value=httpx.Response(
            200,
            text="""<html><body>
            <a href="/job/junior-cyber-be">
                <h3>Junior Cyber Strategy Consultant</h3>
                <div>Belgium</div>
            </a>
            </body></html>""",
        )
    )
    respx.get("https://itsme.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json={"offers": []})
    )
    respx.get("https://www.easi.net/en/jobs").mock(
        return_value=httpx.Response(
            200, text="""
            <html><body>
            <div class="jobs-item jobs__item">
                <a class="jobs-item-link" href="/en/jobs/junior-cyber">
                    <h3 class="jobs-item-title">Junior Cybersecurity Consultant</h3>
                    <div class="jobs-item-offices__location">Brussels</div>
                </a>
            </div>
            </body></html>
            """,
        )
    )
    respx.get("https://www.smals.be/en/jobs/list").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    respx.get("https://www.creamconsulting.com/jobs").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )

    db_path = tmp_path / "e2e.db"
    db_url = f"sqlite:///{db_path}"

    runner = CliRunner()
    from scripts.run_scrape import main as run_scrape_main

    result = runner.invoke(run_scrape_main, ["--db", db_url])
    assert result.exit_code == 0, result.output
    assert "SUMMARY" in result.output
    # Au moins 3 sources avec succès (itsme renvoie 0, ok)
    assert "remotive" in result.output
    assert "nviso" in result.output
    assert "easi" in result.output


@respx.mock
def test_run_scrape_source_filter(tmp_path: Path):
    respx.get("https://www.nviso.eu/jobs/").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )

    db_path = tmp_path / "filter.db"
    runner = CliRunner()
    from scripts.run_scrape import main as run_scrape_main

    result = runner.invoke(
        run_scrape_main, ["--source", "nviso", "--db", f"sqlite:///{db_path}"]
    )
    assert result.exit_code == 0, result.output
    assert "nviso" in result.output
    # Les autres sources ne doivent PAS apparaître dans la summary
    assert "remotive" not in result.output.split("SUMMARY")[1]


def test_run_scrape_unknown_source_warns(tmp_path: Path):
    runner = CliRunner()
    from scripts.run_scrape import main as run_scrape_main

    result = runner.invoke(
        run_scrape_main,
        ["--source", "unknown_source_xyz", "--db", f"sqlite:///{tmp_path / 'x.db'}"],
    )
    assert result.exit_code == 1  # le runner fail proprement via sys.exit(1)


@respx.mock
def test_run_scrape_dry_run_no_db(tmp_path: Path, monkeypatch):
    """En --dry-run, aucune DB n'est créée."""
    respx.get("https://www.nviso.eu/jobs/").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )

    monkeypatch.chdir(tmp_path)  # évite l'effet de bord sur ./data/jobs.db
    runner = CliRunner()
    from scripts.run_scrape import main as run_scrape_main

    result = runner.invoke(run_scrape_main, ["--source", "nviso", "--dry-run"])
    assert result.exit_code == 0, result.output
    # Pas de fichier DB créé dans le cwd
    assert not (tmp_path / "data" / "jobs.db").exists()


# ─── Export CSV ──────────────────────────────────────────────────────────


def test_export_csv_smoke(tmp_path: Path):
    from scripts.export_csv import main as export_main
    from src.storage import JobRepository

    db_url = f"sqlite:///{tmp_path / 'exp.db'}"
    repo = JobRepository(db_url=db_url)
    repo.create_all()
    repo.engine.dispose()

    out = tmp_path / "out.csv"
    runner = CliRunner()
    result = runner.invoke(
        export_main, ["--db", db_url, "--output", str(out), "--min-score", "0"]
    )
    assert result.exit_code == 0
    assert out.exists()
    assert "id,source,external_id" in out.read_text(encoding="utf-8")
