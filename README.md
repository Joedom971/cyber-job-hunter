# 🎯 Cyber Job Hunter

> Scraper automatisé d'offres d'emploi cybersécurité junior, avec scoring personnalisé selon mon profil.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Status](https://img.shields.io/badge/status-Sprint%201%20WIP-orange)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)

---

## 📌 Contexte

Projet personnel développé en parallèle de ma formation **Cybersécurité Blue & Red Team @ BeCode Bruxelles** (Feb-Aug 2026), en vue d'un stage en septembre 2026.

Il sert deux objectifs :
1. **Outil personnel** : automatiser ma veille sur les offres cyber junior alignées avec mon profil
2. **Projet portfolio** : démontrer mes compétences en Python, scraping responsable, automatisation et conception de pipelines de données

## 🏗️ Stack technique

| Layer | Tech |
|---|---|
| Langage | Python 3.11+ |
| Validation | Pydantic v2 |
| HTTP | httpx + hishel (cache ETag) |
| Parsing | feedparser, BeautifulSoup4, lxml |
| Storage | SQLite via SQLModel |
| Logging | loguru |
| CLI | click |
| Dashboard | Streamlit (Sprint 2) |
| Email | Jinja2 + smtplib (Sprint 3) |
| Tests | pytest + respx |
| Lint/Type | ruff + mypy strict |

## 🎯 Profil ciblé (résumé)

- **Postes** : SOC Analyst Junior, Cybersecurity Intern, Blue Team Trainee, Detection Engineer junior, GRC Junior, Threat Intel junior, IR junior, Young Graduate Cyber
- **Localisation** : Bruxelles (priorité 1) > Wallonie / Luxembourg
- **Langues** : FR + EN, ou EN seul, ou FR seul. NL "atout/plus" OK. NL B2/C1 *required* → rejet.
- **Expérience** : Junior / 0-2 ans. Senior / 5+ ans → rejet.

Profil complet dans `config/profile.yaml` (généré en Sprint 1).

## 📊 Sources scraper

### Sprint 1 (en cours)

| # | Source | Type | Statut |
|---|---|---|---|
| 1 | [Remotive](https://remotive.com) | API REST JSON | ✅ Validé recon |
| 2 | [NVISO](https://nviso.eu) | Recruitee API | ✅ Validé recon |
| 3 | [itsme®](https://itsme-id.com) | Recruitee API | ✅ Validé recon |
| 4 | [EASI](https://easi.net) | HTML | ✅ Validé recon |

### Reportés Sprint 2+

CCB, Cream by Audensiel, cybersecurity.lu, Smals, Spotit (Cloudflare), CERT-EU, Spotit (Playwright?), Workday (Proximus, Accenture), Avature (Deloitte), StepStone, Jobat, etc.
Liste complète dans `config/sources.yaml`.

## 🧠 Scoring (résumé)

```
+30 si poste cible dans le titre
+15 si "junior/stage/intern/trainee" dans la description
+10 si "young graduate" dans le titre OU "graduate program" dans la description
+5 par mot-clé technique matché (cap +30)
+10 Bruxelles / +5 Wallonie / +5 Luxembourg
+10 FR+EN / +8 EN seul / +5 NL "nice to have"
−5 "Bachelor required" sans alternative
−20 "Master mandatory" sans alternative
−10 "3+ years"

Rejet (score=0) :
- "5+ years"
- "Senior / Lead / Manager"
- NL B2/C1 required sans alternative EN
```

Implémentation : `src/scoring.py` (à venir).

## 🛡️ Politesse de scraping (engagements anti-ban)

- `User-Agent` honnête : `JobHunterBot/1.0 (+contact: ...)`
- Respect `robots.txt`
- Rate limit 2-5s entre requêtes même domaine + jitter aléatoire
- Backoff exponentiel sur erreurs (5s → 15s → 45s, 3 retries max)
- Circuit breaker par domaine (3 erreurs 4xx/5xx d'affilée → désactive 1h)
- Détection challenges anti-bot (Cloudflare, captcha) → abort propre
- Cache HTTP local (ETag / Last-Modified) → évite les re-fetch inutiles
- Pas plus de N pages par site, pas plus d'1 run / 12h / source
- LinkedIn : désactivé par défaut (ToS)

## 🚀 Quickstart (à venir)

```bash
git clone https://github.com/Joedom971/cyber-job-hunter.git
cd cyber-job-hunter
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # remplis les valeurs
python scripts/init_db.py
python scripts/run_scrape.py
streamlit run dashboard/app.py  # Sprint 2
```

## 🗺️ Roadmap

- **Sprint 1** *(en cours)* — Bootstrap, modèles, scoring, filters, storage SQLite, 4 scrapers (Remotive, NVISO, itsme, EASI), tests, export CSV
- **Sprint 2** — Dashboard Streamlit, détection nouvelles offres, notifs macOS, +3 scrapers BE
- **Sprint 3** — Email digest Gmail SMTP, +3 job boards, cron launchd
- **Sprint 4** — Génération lettres de motivation, sources LU + EU, scoring ML, LinkedIn (avec safeguards)

## ⚠️ Sécurité

- **Ne JAMAIS commit `.env`** : il contient un App Password Gmail. Toujours utiliser `.env.example` comme template.
- **Compte Gmail dédié** pour l'email digest : si l'App Password leak, seul ce compte robot est compromis (pas le perso).
- En cas de doute : régénérer un App Password sur https://myaccount.google.com/apppasswords.
- Voir aussi `.gitignore` pour la liste complète des fichiers sensibles exclus.

## 📜 Licence

[MIT](LICENSE) — © 2026 Johan-Emmanuel Hatchi

## 🤝 Contributions

Projet personnel, mais issues / suggestions bienvenues.
