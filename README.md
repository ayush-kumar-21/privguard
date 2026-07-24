# PrivGuard

**A Differentially Private Query Gateway for Healthcare Analytics.**
BCSE307L Database Systems course project — Django REST Framework + PostgreSQL + React.

PrivGuard lets analysts run aggregate SQL over patient data while the *system* — not the
analyst — enforces privacy: calibrated differential-privacy noise, a per-analyst privacy
budget recorded in an append-only hash-chained ledger, and role-based column access.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 18 + Vite + TypeScript + TailwindCSS |
| Backend | Django + Django REST Framework |
| Database | PostgreSQL (accessed only via the Django ORM) |
| Auth | DRF SimpleJWT (core); Google OAuth (optional add-on) |
| Password hashing | Argon2 |
| DP / query layer | Python (NumPy/SciPy) + `sqlglot` for SQL parsing |
| Ops | Docker Compose · GitHub Actions · Render/Railway |

## Project layout

```
privguard/
├── backend/                 # Django project
│   ├── privguard/           # settings, urls, wsgi/asgi
│   ├── accounts/            # SYS_PRINCIPAL (custom user), roles, grants   (Subsystem C)
│   ├── catalog/             # sys_* structural catalog + privacy policy    (Subsystems A, B)
│   ├── budget/              # budget account + hash-chained ledger          (Subsystem D)
│   ├── audit/               # query log, fingerprint, noise cache, overlap  (Subsystem E)
│   ├── safehealth/          # regions…visits…lab_results demo dataset       (Subsystem F)
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                # React + Vite + TS + Tailwind
├── docker-compose.yml       # db + api + ui
├── PrivGuard_ER_Documentation.md    # 28 entities / 39 relationships
└── PrivGuard_Rubric_Alignment.md    # how the build maps to the grading rubric
```

The 28 Django models correspond 1:1 to the ER model (see `PrivGuard_ER_Documentation.md`,
Figures 1–5). `accounts.Principal` is `AUTH_USER_MODEL` — the single users table.

## Quick start (Docker — one command)

```bash
docker compose up --build
# UI  → http://localhost:8080
# API → http://localhost:8000/api/health/
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
