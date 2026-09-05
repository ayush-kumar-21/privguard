# PrivGuard

**A differentially private query execution engine with budget-aware storage semantics.**
BCSE307L Database Systems course project.

PrivGuard lets analysts run aggregate SQL over patient data while the *system* — not the
analyst — enforces privacy: calibrated differential-privacy noise, a per-analyst privacy
budget recorded in an append-only hash-chained ledger, and role-based column access.

## Status: mid re-architecture

The project is being rebuilt to `privguard_blueprint.png` — a from-scratch **Rust**
database engine (slotted pages, CLOCK buffer pool, B+ tree, a Volcano executor with the
DP operators *inside the plan*) behind a **FastAPI** gateway.

The strategy is **build alongside, cut over at the end**: the working Django stack keeps
running throughout, so `run.bat` always brings up a demoable app. Django is retired only
once the engine passes the oracle.

**Progress and the resume point live in [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).**
The frozen contract both implementations are built against is [`SPEC.md`](SPEC.md).

| Subsystem | State |
|---|---|
| `SPEC.md` — frozen specification | v1.0 |
| `oracle/` — Python DP reference implementation | 93 tests passing |
| `engine/` — Rust workspace, zCDP accounting | 43 tests passing |
| `engine/` — storage, planner, executor, pgwire | Phase 2 onward |
| `gateway/` — FastAPI | Phase 4.4 |
| `backend/` — Django (legacy) | running; retired in Phase 5.5 |

## Tech stack

| Layer | Target (blueprint) | Today |
|---|---|---|
| Frontend | React 18 · Vite · TS · Tailwind · Monaco · react-flow | React 18 · Vite · TS · Tailwind · CodeMirror |
| Gateway | FastAPI · Uvicorn · Pydantic v2 · python-jose · argon2 | Django · DRF · SimpleJWT · argon2 |
| Engine | Rust — `sqlparser-rs`, `pgwire`, `tokio`, `blake3`, `aes-gcm` | Python `sqlglot` + Django ORM |
| Storage | Custom heap files · B+ tree · 8 KiB pages · self-hosted catalog | PostgreSQL |
| Reference | Python 3.11 · NumPy/SciPy · pytest · criterion · proptest | `oracle/` (stdlib-only), pytest |
| Ops | Docker Compose · GitHub Actions | same |

## Project layout

```
privguard/
├── engine/                  # Rust — the core deliverable, DB internals from scratch
│   └── crates/
│       ├── privguard-core/      errors, column types, RIDs
│       ├── privguard-storage/   pages · heap files · buffer pool · B+ tree
│       ├── privguard-catalog/   self-hosted sys_* catalog
│       ├── privguard-sql/       parser & binder
│       ├── privguard-plan/      logical planner + Volcano physical plan
│       ├── privguard-privacy/   zCDP · mechanisms · DP operators · ledger
│       ├── privguard-crypto/    AES-256-GCM columns · blake3 chaining
│       ├── privguard-wire/      pgwire v3 server
│       └── privguardd/          the daemon binary
├── oracle/                  # Python DP reference implementation (stdlib only)
├── gateway/                 # FastAPI gateway                       (Phase 4.4)
├── frontend/                # React + Vite + TS + Tailwind
├── backend/                 # Django project (legacy — retired in Phase 5.5)
│   ├── accounts/            # SYS_PRINCIPAL (custom user), roles, grants   (Subsystem C)
│   ├── catalog/             # sys_* structural catalog + privacy policy    (Subsystems A, B)
│   ├── budget/              # budget account + hash-chained ledger         (Subsystem D)
│   ├── audit/               # query log, fingerprint, noise cache, overlap (Subsystem E)
│   └── safehealth/          # regions…visits…lab_results demo dataset      (Subsystem F)
├── docker-compose.yml       # db + api + ui
├── SPEC.md                          # frozen engine specification
├── IMPLEMENTATION_PLAN.md           # phase checklist + resume point
├── PrivGuard_Project_Proposal.md    # title, objectives, scope, team, Gantt chart
├── PrivGuard_ER_Documentation.md    # 28 entities / 39 relationships
├── PrivGuard_Data_Dictionary.md     # every table, column, key, constraint, index
├── PrivGuard_Rubric_Alignment.md    # how the build maps to the grading rubric
└── DEPLOYMENT.md                    # cloud deploy, Docker Hub, backup/restore drill
```

The 28 Django models correspond 1:1 to the ER model (see `PrivGuard_ER_Documentation.md`,
Figures 1–5). `accounts.Principal` is `AUTH_USER_MODEL` — the single users table.

## Quick start

```bat
run.bat            :: build and start db + api + ui, then print the URLs
```

```
App  → http://localhost:8080
API  → http://localhost:8000/api/
```

Sign in with any seeded principal, password `privguard123`. Each one is seeded
with a genuine query history — the budgets below were spent by really running
queries through the DP gateway, not by writing rows:

| User | Role | State |
|---|---|---|
| `alice` | analyst | ~21% of a ρ = 5.00 budget spent, populated burn-down |
| `frank` | analyst | ~90% of a ρ = 0.20 budget spent — his next query is refused |
| `dave` | dba | every surface, including policy administration |
| `erin` | auditor | audit log and ledger |

`run.sh` is the POSIX equivalent. Both start Docker Desktop if it is not already
running, and retry the image build — `npm install` and `pip install` inside a
Docker build are the flakiest step on a slow connection.

### Working on the blueprint build

```bat
run.bat check      :: oracle tests + engine build/test (no containers started)
run.bat oracle     :: Python DP oracle suite            (uv, no Docker)
run.bat engine     :: build and test the Rust engine    (Docker; no host Rust needed)
run.bat data       :: regenerate data/safehealth/*.csv  (deterministic, seed 42)
```

## Local development

**Backend**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Python 3.12 recommended
pip install -r requirements.txt
cp .env.example .env                                    # then edit
# start PostgreSQL (or: docker compose up db)
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver                              # http://localhost:8000
```

**Frontend**
```bash
cd frontend
npm install
npm run dev                                             # http://localhost:5173
```

## Auth endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/register/` | Create a principal |
| POST | `/api/auth/login/` | Obtain JWT access + refresh |
| POST | `/api/auth/refresh/` | Refresh access token |
| POST | `/api/auth/logout/` | Blacklist refresh token |
| GET | `/api/auth/me/` | Current principal + roles |

## Database backup & restore (PostgreSQL native tools)

```bash
pg_dump -U privguard -d safehealth -F c -f backup.dump   # backup
pg_restore -U privguard -d safehealth --clean backup.dump # restore
```

## Rubric mapping

See `PrivGuard_Rubric_Alignment.md` for the section-by-section mapping to the 30-mark
rubric (ER design, ORM CRUD/joins/views/aggregates, stored procedure + trigger via the
budget ledger, RBAC on a single users table, Docker, and deployment).
