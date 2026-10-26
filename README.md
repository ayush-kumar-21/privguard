# PrivGuard

**A differentially private query gateway for healthcare analytics.**
BCSE307L Database Systems course project (Dr. Deepika J, Fall 2026-27).

PrivGuard lets analysts run aggregate SQL over patient data while the *system* — not the
analyst — enforces privacy: calibrated differential-privacy noise, a per-analyst privacy
budget recorded in an append-only hash-chained ledger, and role-based column access.

> **What is graded here:** the **Django + PostgreSQL + React** application in `backend/`,
> `frontend/` and `db/`. The Rust engine in `engine/` and the Python reference in `oracle/` are a
> separate *research extension* (future work) and are not part of the submission.

## Links

| | |
|---|---|
| Live app | **<LIVE_URL>** *(fill in after deploying — see [DEPLOYMENT.md](DEPLOYMENT.md))* |
| Docker Hub | **<DOCKERHUB_USER>/privguard-api** · **<DOCKERHUB_USER>/privguard-ui** |
| Repository | https://github.com/ayush-kumar-21/privguard (private) |

## Technology stack (as submitted)

| Layer | Technology |
|---|---|
| Frontend | React 18 · Vite · TypeScript · Tailwind CSS · TanStack Query · Recharts |
| Backend | Django · Django REST Framework |
| Database | PostgreSQL 16 |
| ORM | Django ORM (raw SQL only for stored procedures / triggers / views) |
| Authentication | JWT (SimpleJWT: login, refresh, logout with blacklist, `/me`) |
| Authorization | RBAC — single users table (`sys_principal`), roles via `sys_role` |
| Security | Argon2 password hashing · ORM-parameterised queries · secrets in `.env` |
| Ops | Docker · Docker Compose · GitHub Actions CI · pg_dump / pg_restore |

## Requirement coverage

| Brief section | Where it lives |
|---|---|
| ER diagram, schema, data dictionary | [`docs/er/`](docs/er) (PNG + Mermaid), `PrivGuard_ER_Documentation.md`, `PrivGuard_Data_Dictionary.md` |
| Tables / constraints / 3NF / indexes | 30 tables in Django models + migrations (`backend/*/migrations`) |
| Views, stored procedures, triggers | `backend/budget/migrations/0002…0005` (`v_*`, `sp_*`, `trg_*`) |
| Transactions | `backend/privacy/service.py` (`transaction.atomic` + reserve/settle procedures) |
| CRUD, search, filter, pagination | DRF viewsets in `backend/api/views.py`; React data screens in `frontend/src/routes` |
| Validation, exception handling | DRF serializers + `backend/api/exceptions.py`; `ResourceForm` validation, `ErrorBoundary` |
| Auth, RBAC | `backend/accounts`, `backend/api/permissions.py`, `frontend/src/components/states/RoleGate.tsx` |
| Backup & restore | `db/backup.sh`, `db/restore.sh`, `db/verify_restore.sh` |
| Docker, Docker Hub, deployment | `docker-compose.yml`, `*/Dockerfile`, `render.yaml`, [DEPLOYMENT.md](DEPLOYMENT.md) |
| CI/CD | `.github/workflows/ci.yml`, `docker-publish.yml` |

Full rubric mapping: [`PrivGuard_Rubric_Alignment.md`](PrivGuard_Rubric_Alignment.md).
The delivery checklist and progress: [`docs/RUBRIC_COMPLETION_PLAN.md`](docs/RUBRIC_COMPLETION_PLAN.md).
Security hardening of the DP gateway (findings, attack classes, fixes): [`docs/DP_HARDENING.md`](docs/DP_HARDENING.md).
Measured before/after evidence (collusion, partition δ, timing, direct database access): [`docs/EVALUATION.md`](docs/EVALUATION.md) — reproduce with `./eval/run_all.sh`.

## Per-patient privacy budgets

Besides each analyst's budget, every **patient** has their own allowance shared by all analysts
(`sys_patient_budget`). A query charges only the patients it can affect, in the same flow as the
analyst's charge; a patient whose allowance is spent is left out of later queries; a failed query is
refunded. Each patient has an append-only, hash-chained ledger (`sys_patient_ledger`). This is what
makes opening extra analyst accounts worthless for collusion.

**Security rule:** these rows show exactly which patients a query touched, so only the **DBA** can read
them, a DBA or superuser **cannot run queries**, and an account cannot hold `dba` together with a query
role. Design, soundness notes and the limits that remain: `docs/PATIENT_ACCOUNTING_DESIGN.md`.

## Project layout (submission)

```
privguard/
├── backend/                 # Django project
│   ├── accounts/            # sys_principal (custom user), roles, grants     (Subsystem C)
│   ├── catalog/             # sys_* structural catalog + privacy policy      (Subsystems A, B)
│   ├── budget/              # budget account + hash-chained ledger           (Subsystem D)
│   ├── audit/               # query log, fingerprint, noise cache, overlap   (Subsystem E)
│   ├── safehealth/          # regions…visits…lab_results demo dataset        (Subsystem F)
│   ├── privacy/             # DP query service (sqlglot parse → ORM → noise)
│   ├── api/                 # DRF viewsets, permissions, error handler
│   └── tests/               # pytest suite (constraints, ledger, API/RBAC)
├── frontend/                # React + Vite + TS + Tailwind
├── db/                      # backup.sh · restore.sh · verify_restore.sh
├── docs/                    # ER diagrams, completion plan
├── docker-compose.yml       # db + api + ui
└── DEPLOYMENT.md            # cloud deploy, Docker Hub, backup/restore drill
```

Research extension (not graded): `engine/` (Rust DB engine), `oracle/` (Python DP reference),
`SPEC.md`, `IMPLEMENTATION_PLAN.md`, `PrivGuard_Project_Plan.md`.

The 30 Django models correspond 1:1 to the ER model. `accounts.Principal` is
`AUTH_USER_MODEL` — the single users table; roles are resolved through `sys_principal_role`,
never through per-role tables. (`staff` is hospital HR data — name, department, hire date —
not a login table.)

## Installation and execution

**Docker (recommended)**

```bat
run.bat            :: Windows — build and start db + api + ui, then print the URLs
./run.sh           :: Linux / macOS equivalent
```

```
App  → http://localhost:8080
API  → http://localhost:8000/api/
```

Sign in with a seeded principal, password `privguard123`:

| User | Role | State |
|---|---|---|
| `alice` | analyst | ~21% of a ρ = 5.00 budget spent, populated burn-down |
| `frank` | analyst | ~90% of a ρ = 0.20 budget spent — his next query is refused |
| `dave` | dba | every surface, including policy administration |
| `erin` | auditor | audit log and ledger |

Budgets were spent by really running queries through the DP gateway, not by writing rows.

**Tests**

```bash
cd backend && pytest                 # needs PostgreSQL (DATABASE_URL in .env)
cd frontend && npm run build         # typecheck + production build
./run.sh oracle                      # DP oracle, 138 tests (uses uv; no host pytest needed)
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
