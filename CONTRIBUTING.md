# Contributing

Projet personnel mais les issues / PRs / suggestions sont les bienvenues. Quelques conventions pour garder le code cohérent.

## Setup local

```bash
git clone https://github.com/Jhatchi/cyber-job-hunter.git
cd cyber-job-hunter
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest  # vérifie que tout passe (357 tests)
```

## Conventions

### Commits — Conventional Commits

Format : `<type>(<scope>): <description impérative courte>`

Types utilisés :
- `feat(...)` : nouvelle fonctionnalité
- `fix(...)` : correction de bug
- `chore(...)` : maintenance, deps, config (pas de fonctionnalité utilisateur)
- `refactor(...)` : restructuration sans changement de comportement
- `test(...)` : ajout/modification de tests uniquement
- `docs(...)` : documentation

Scopes courants : `scrapers`, `scoring`, `filters`, `dashboard`, `storage`, `config`, `models`.

Exemples de mes commits :

```
feat(scrapers): add NVISO HTML scraper after Recruitee migration
fix(filters): refine Dutch detection to handle accent-insensitive patterns
chore(scrapers): use Proton Pass alias for User-Agent contact email
```

### Code style

- **Type hints** sur toutes les signatures publiques.
- **`ruff`** pour le linting (config dans `pyproject.toml`).
- **`mypy`** strict (toléré : `Any` quand justifié, type ignore commenté).
- **Docstrings** en français pour les modules / classes publiques.
- **Comments** parcimonieux : un commentaire explique le *pourquoi*, pas le *quoi*.

```bash
ruff check src/ dashboard/ tests/
ruff format src/ dashboard/ tests/
mypy src/
```

### Tests

- Tout nouveau module doit être testé.
- Cible : **80 %+ coverage** sur les modules métier (`src/`, `dashboard/data.py`).
- **`respx`** pour mocker `httpx` dans les scrapers — ne JAMAIS hit le réseau dans les tests unitaires.
- Tests d'intégration scrapers → DB → scoring : utiliser des fixtures DB en `tmp_path`.
- Tests de la UI Streamlit non requis (couvertes par smoke tests).

### Ajouter un nouveau scraper

1. **Recon HTTP read-only** sur le site cible — vérifier `robots.txt`, structure HTML/API
2. Créer `src/scrapers/<nom>.py` qui hérite de `BaseScraper`
3. Implémenter uniquement `fetch_jobs(self, page) -> tuple[list[JobBase], bool]`
4. Ajouter `JobSource.<NOM>` dans `src/models.py`
5. Enregistrer dans `src/scrapers/__init__.py` (`SCRAPER_FACTORIES`)
6. Configurer dans `config/sources.yaml` (avec `enabled: true`, `notes:` documentés)
7. Ajouter `tests/test_<nom>.py` avec fixtures HTML/JSON représentatives + ≥6 tests
8. Lancer `python scripts/run_scrape.py --source <nom>` pour valider live

## Sécurité

- ⚠️ **Ne pas committer** : `.env`, `data/jobs.db`, logs, secrets, payloads HTTP réels avec PII
- Si tu trouves un secret commité par erreur : **révoquer** immédiatement, puis nettoyer l'historique git (BFG ou `git filter-repo`)
- LinkedIn : n'ajouter le scraper qu'avec safeguards stricts (rate 1/3s, max 200/j, abort sur détection bot, opt-in via flag config)

## Code de conduite (light)

- Pas de bot abuse — respecter les `robots.txt`, rate limiter, et abandonner gracieusement quand un site veut pas de nous
- Le projet collecte des offres publiques d'emploi ; aucune donnée personnelle de recruteurs n'est stockée

## Questions / contact

Issue GitHub, ou via l'adresse de contact du `User-Agent` (alias Proton Pass).
