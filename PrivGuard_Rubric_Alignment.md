# PrivGuard — Framed for BCSE307L (Database Systems)

**Course:** BCSE307L Database Systems · **Faculty:** Dr. Deepika J · **Slots:** L11+L12 / L43+L44
**Marks:** 30 + up to 3 bonus · **Team:** 2–3 · **Duration:** full semester (~14 weeks)

This document re-frames the PrivGuard project so it is read *as a BCSE307L submission*. It maps every
deliverable onto the rubric's six graded sections, states the one decision that makes the project
compliant, and lists the concrete gaps to close. It sits alongside:

- `PrivGuard_ER_Documentation.md` — the ER model (rubric-ready, wins Section 2 outright).
- `PrivGuard_Project_Plan.md` — the *research* framing (Rust engine). **Superseded for grading** by this
  document; keep it only as the "Future Work / research vision" appendix.

---

## 0. The one decision that reframes everything

> **PrivGuard is a Django + PostgreSQL + React web application. Differential privacy lives in the
> application/service layer, not in a hand-written storage engine.**

The `PrivGuard_Project_Plan.md` builds a database *from scratch* in Rust (heap files, buffer pool, B+
tree, PostgreSQL wire protocol, CP-ABE). That is a superb research project and a **failing** course
submission, because the rubric explicitly grades *use of a stock RDBMS through an ORM*, plus stored
procedures, triggers, views, and transactions — none of which exist when you write your own engine.

The pivot is not a downgrade; it *deletes* the hardest, non-graded work and hands you the graded
features for free:

| Rust-plan component | Fate under the rubric | Why |
|---|---|---|
| Slotted pages, buffer pool (CLOCK), heap files | **Cut** | PostgreSQL *is* the storage engine |
| B+ tree index | **Cut** → `CREATE INDEX` in Postgres | Section 2/3 "indexes" satisfied by real Postgres indexes |
| pgwire wire protocol, PEP-249 driver | **Cut** | The React UI + DRF API is the client surface |
| Volcano executor, physical plan operators | **Reframed** → DP **service functions** over ORM querysets | Section 3 "aggregate queries / joins" via Django ORM |
| CP-ABE crypto, envelope encryption | **Trim** → column-level encryption of `mrn` only (bonus) | Keep the *policy* (raw/dp/redacted/denied); drop bespoke crypto |
| Rust `privlib` DP math | **Keep**, in **Python/NumPy** inside Django | The novelty; runs in the service layer |
| Hash-chained budget ledger | **Keep** → Postgres **trigger** (`pgcrypto`) | Becomes the rubric's mandatory trigger |
| Two-phase RESERVE/COMMIT | **Keep** → Postgres **stored procedure** + Django `transaction.atomic` | Becomes the mandatory stored proc + the transactions demo |
| Budget burn-down, audit | **Keep** → Postgres **views** | Becomes the mandatory views + dashboard + audit-log bonus |
| SQL console (`sql_text`) | **Keep, sandboxed** → parse with `sqlglot`, execute via ORM | See §5 (SQL-injection reconciliation) |

**Net effect:** the differentially-private query gateway *is* the "advanced database application." The
privacy machinery is exactly what supplies the trigger, the stored procedure, the views, the
multi-statement transaction, and the audit log the rubric asks for.

---

## 1. Rubric scorecard (target: 30/30 + 3)

| # | Section | Marks | PrivGuard deliverable | Status |
|---|---|---|---|---|
| 1 | Problem Statement & Project Planning | 2 | Title, problem, objectives, scope, team roles, stack, deliverables, Gantt | **Draft from §7** |
| 2 | Database Design (ER, ≥4 entities, ≥3 rel., schema, keys, constraints, 3NF) | 6 | 28 entities / 39 relationships / 5 figures / full 3NF justification | **Done** (`PrivGuard_ER_Documentation.md`) |
| 3 | Database Implementation (CRUD, joins, views, aggregates, transactions, SP + trigger, ORM) | 8 | Django ORM over 28 Postgres tables; DP service layer; ledger trigger; reserve SP; budget/audit views | **Done** |
| 4 | Application Development (dashboard, search, filter, pagination, validation, exceptions, responsive UI) | 4 | React: SQL Console, Budget Dashboard, Policy Admin, Audit, CRUD admin, Attack Lab | **Build** |
| 5 | Auth, Authz & DB Security (OAuth/JWT/session, ≥2 roles, single users table, ORM authz, hashing, SQLi, secrets) | 5 | `sys_principal` = single users table; 4 roles; DRF-JWT; Argon2; ORM-parameterised; `.env` | **Done** (tests in `backend/tests/test_api.py`) |
| 6 | Professional Practices (GitHub, backup/restore, Docker, Docker Hub, cloud deploy) | 5 | Private repo, main/dev, pg_dump/pg_restore, Docker Compose, Docker Hub, Render/Railway | **Done** except Docker Hub push + live URL (external) |
| B | Bonus (CI/CD, custom domain, one best practice) | +3 | GitHub Actions (done) + custom domain (external) + **audit logging** (done) | **+2 secured, +1 pending domain** |

---

## 2. Section 2 — Database Design (6 marks) — already earned

The rubric's *mandatory* design bar and PrivGuard's answer:

| Requirement | Minimum | PrivGuard |
|---|---|---|
| Primary entities | 4 | **28** |
| Relationships | 3 | **39** (incl. 4 M:N resolved to associative entities, 1 recursive) |
| Tables (in implementation) | 8–10 | **28** |
| Every table from the ER diagram | yes | yes — Figures 1–5 |
| Primary key on every table | yes | yes |
| Foreign keys | appropriate | yes (all FKs enumerated in ER doc §3) |
| Constraints (NOT NULL, UNIQUE, CHECK, DEFAULT) | yes | present in `PrivGuard_Project_Plan.md` §3 DDL |
| Normalisation | 3NF+ | **3NF, justified**, incl. the one guarded denormalisation (`rho_spent`) |
| Indexes | where needed | `idx_ledger_account` + PK/UK/FK indexes + `idx_visits_admit`, `idx_visits_patient` |

**Two fixes before you submit the schema:**

1. **Retype for PostgreSQL.** The DDL uses `DOUBLE`, `BINARY(32)`, `VARBINARY(512)`. Postgres wants
   `DOUBLE PRECISION`, `BYTEA`, `BYTEA`. In Django models these become `FloatField`, `BinaryField`.
2. **Keep the ER↔table mapping tight.** The examiner note in the rubric — *"tables created without a
   corresponding ER diagram will not be evaluated"* — means every Django model must trace to an entity
   in Figures 1–5. It does; keep the cardinality table (ER doc §3) beside the picture in the report.

---

## 3. Section 3 — Database Implementation (8 marks) — the build

Every rubric line item, mapped to a concrete PrivGuard feature. **All routine access goes through the
Django ORM;** raw SQL is used only where the rubric explicitly permits it (stored procedures, triggers,
performance-critical queries).

| Rubric item | PrivGuard implementation |
|---|---|
| Database creation | `safehealth` Postgres DB; 28 tables via Django migrations |
| ≥ 8–10 related tables | 28 tables, all FK-linked |
| CRUD | Manage patients, visits, roles, column-grants, budgets, policies via ORM + React forms |
| Joins | Star-schema DP queries: `visits ⨝ departments ⨝ patients`; audit joins `sys_query ⨝ sys_principal ⨝ sys_query_fingerprint` |
| Aggregate queries | The DP core: `COUNT / SUM / AVG / GROUP BY` over `visits`, computed via ORM `annotate()/aggregate()` then noised |
| Views | `v_budget_status` (account + spent + remaining + ε); `v_query_audit`; `v_visit_summary` |
| Transactions | **Two-phase budget protocol**: `transaction.atomic()` + `select_for_update()` on the account row → RESERVE → execute → COMMIT/ABORT |
| Stored procedure (≥1) | `sp_reserve_budget(account, rho_req)` in PL/pgSQL — atomic check-and-hold; and/or `fn_verify_ledger(account)` that replays the hash chain |
| Trigger (≥1) | `BEFORE INSERT` on `sys_budget_ledger`: compute `entry_hash = digest(prev_hash || row)` via `pgcrypto`, and a `BEFORE UPDATE/DELETE` trigger that `RAISE EXCEPTION` to enforce append-only |
| Indexes for performance | `idx_ledger_account`, `idx_visits_admit`, `idx_visits_patient`, unique indexes on `ast_hash`, `(principal_id, db_id)` |
| ORM integration | Django ORM is the sole data-access path for the API; the DP layer builds querysets, never string-concatenated SQL |

**Why this scores full marks:** the trigger, stored procedure, views, and transaction are *not*
contrived textbook add-ons — they are the privacy budget's integrity mechanism. That is exactly the
"advanced database features where raw SQL is necessary" the rubric carves out.

---

## 4. Section 4 — Application Development (4 marks)

The research plan's UI is analyst-console-centric. Add the plain CRUD screens the rubric checks for.

| Rubric item | PrivGuard screen |
|---|---|
| Responsive UI | React 18 + Vite + TS + Tailwind |
| Login page | JWT / Google OAuth login |
| Dashboard | Budget burn-down (Recharts): cumulative ρ & equivalent ε, red band at exhaustion |
| CRUD | Admin tables for Patients, Visits, Roles, Column-Grants, Budgets, Privacy Policies |
| Search | Search patients (zip3, insurance), queries (status, principal), audit log |
| Filtering | Filter visits by department / admission_type / date; queries by status |
| Pagination | DRF pagination on every list endpoint; paginated React grids |
| Form validation | Pydantic/DRF serializers + client-side validation on policy & grant forms |
| Exception handling | DRF exception handler → typed JSON errors; React error boundaries; budget-exhausted and policy-denied surfaced cleanly |

Keep the showpiece screens too — **Policy Admin** (columns × roles access-mode grid), **Audit**, and
**Attack Lab** (run averaging/differencing attacks with DP on vs. off). They demo the novelty; the CRUD
screens tick the rubric.

---

## 5. Section 5 — Auth, Authorization & DB Security (5 marks) — read carefully

The rubric flags this as *"where all students make the mistake."* Three points to get exactly right:

**(a) Single users table — you already comply, state it explicitly.**
`sys_principal` is the one and only users table. Every human and service account is a row in it. Roles
are a lookup (`sys_role`) linked by a mapping (`sys_principal_role`). This is the rubric's *"Role /
RoleID column"* pattern in its normalized form — it is **not** the forbidden anti-pattern of separate
`Admin` / `Clinician` / `Auditor` tables. In the report, show `sys_principal` and say: *"all users live
in one table; role is resolved via `sys_principal_role`, never via per-role tables."*
**Locked:** `sys_principal` is the Django `AUTH_USER_MODEL`, so login/JWT key off it directly (see §10).

**(b) Roles & RBAC — exceeds the minimum.**
Minimum is 2 roles; PrivGuard has **4** (`dp_analyst`, `clinician`, `auditor`, `dba`).
- Role-based **menu**: Policy Admin & user management visible only to `dba`.
- Role-based **page**: Attack Lab / raw-data views gated by role.
- Role-based **API**: DRF permission classes per endpoint.
- Role-based **data**: column access modes `raw / dp / redacted / denied` from `sys_column_grant`.

**(c) Database security.**
- **Password hashing:** Argon2 (`argon2-cffi`, Django `PASSWORD_HASHERS`) — matches the rubric's Argon2
  recommendation. Never store `pwd_hash` in plaintext.
- **SQL-injection prevention — the design tension you must resolve.** PrivGuard's premise is that
  analysts submit SQL text (`sys_query.sql_text`). *Never execute that string against Postgres.* Instead:
  parse it with **`sqlglot`** into an AST, validate it against the allowed subset (SELECT + aggregates +
  star-schema joins only), and translate it into **parameterised Django ORM querysets**. The user's SQL
  is *data to the parser*, never SQL to the database. State this in the threat model — it converts your
  biggest apparent SQLi liability into a security feature.
- **Secrets:** DB password, JWT secret, OAuth client secret, `server_secret` (noise seed) live in
  `.env`, loaded via `django-environ`; `.env` is git-ignored. No hardcoded secrets.

---

## 6. Section 6 — Professional Development Practices (5 marks)

Status: **done** except the external items marked below (live URL, Docker Hub push, custom domain),
which need the team's own cloud accounts. Track them in `docs/RUBRIC_COMPLETION_PLAN.md`.

| Rubric item | Action |
|---|---|
| Private GitHub repo | `git init`, create private repo, add all teammates as collaborators |
| Branches | `main` + `dev` (feature branches → PR → `dev` → `main`) |
| ≥ 10 meaningful commits | Commit per milestone; meaningful messages |
| README | Description, install steps, tech stack, execution steps |
| `.gitignore` | Exclude `.env`, `__pycache__`, `node_modules`, `*.sqlite3`, secrets, keys |
| Backup & restore | **`pg_dump safehealth > backup.sql`** / **`pg_restore`** (or `psql < backup.sql`); document both |
| Docker | `docker-compose.yml`: `db` (postgres), `api` (django), `ui` (react/nginx) |
| Docker Hub | Push `privguard-api` and `privguard-ui` images |
| Cloud deploy | Render / Railway / Fly.io; submit **Live URL**; managed Postgres (Supabase/Railway) |

**Bonus (+3), all within reach:**
- **CI/CD (+1):** GitHub Actions running `pytest` + `npm test` + lint on PRs.
- **Custom domain (+1):** DuckDNS or similar on the deployed URL.
- **One best practice (+1):** **Audit logging is already core** (`sys_query` + hash-chained
  `sys_budget_ledger`) — claim it. Automated testing and performance-optimised indexes are backups.

---

## 7. Section 1 — Problem Statement & Project Planning (2 marks) — draft

- **Title:** *PrivGuard — A Differentially Private Query Gateway for Healthcare Analytics.*
- **Problem statement:** Hospitals must let analysts query patient data without leaking any individual's
  record. PrivGuard is a web application over PostgreSQL that answers aggregate SQL queries with
  calibrated differential-privacy noise, enforces a per-analyst privacy budget with an append-only,
  hash-chained ledger, and applies role-based column access — so *privacy is enforced by the system,
  not trusted to the analyst.*
- **Objectives:** (1) 3NF schema of catalog + SafeHealth data; (2) DP aggregate service (Laplace /
  Gaussian / discrete-Laplace) with contribution bounding; (3) budget ledger with trigger-enforced
  integrity and two-phase transactions; (4) RBAC + column policies; (5) React dashboards + attack demo;
  (6) Dockerised cloud deployment.
- **Scope:** star-schema aggregate queries on one privacy unit (`patient_id`); joins to public
  dimension tables only. *Out of scope:* cross-privacy-unit joins, timing side channels, malicious DBA.
- **Team & responsibilities (fill in names):** *(A)* DB schema, migrations, triggers/SP/views;
  *(B)* DP service layer + budget ledger + attack lab; *(C)* React UI + auth + deployment. For a team of
  two, merge (C) into (A)/(B).
- **Tech stack:** React 18 + Vite + TS + Tailwind · Django REST Framework · PostgreSQL · Django ORM ·
  JWT/Google OAuth · Argon2 · Python DP lib (NumPy/SciPy) · `sqlglot` · Docker · Render.
- **Deliverables:** ER model (done), schema + migrations, DP API, React app, Docker images, live URL,
  README, demo script, report.
- **Gantt:** condense the 14-week plan in §8.

---

## 8. Condensed timeline (Gantt source)

| Wk | Milestone |
|---|---|
| 1 | Proposal, ER finalised, `datagen.py` (synthetic SafeHealth), repo + CI skeleton |
| 2 | Django project, 28 models + migrations, admin CRUD, seed data |
| 3 | Auth: custom user (`sys_principal`), Argon2, JWT/OAuth, RBAC permissions |
| 4 | Python DP lib (Laplace/Gaussian/discrete-Laplace, clamp, contribution bound) + tests |
| 5 | DP query service: `sqlglot` parse → ORM aggregate → noise; COUNT/SUM/AVG |
| 6 | Budget: account/ledger models, **trigger** (hash chain), **stored proc** (reserve), **transactions** |
| 7 | GROUP BY DP partition selection; noise memoization (defeats averaging attack) |
| 8 | Column policies (raw/dp/redacted/denied), predicate-overlap detection |
| 9 | **Views** (budget/audit/summary); DRF endpoints; search/filter/pagination |
| 10 | React: login, dashboard, SQL console, policy admin, CRUD screens |
| 11 | React: audit view, Attack Lab; validation + error handling; responsive pass |
| 12 | Docker Compose, Docker Hub, cloud deploy, custom domain, pg_dump/restore drill |
| 13 | Evaluation (utility vs. ε, attack resistance), report, README, demo script |
| 14 | Buffer, polish, viva prep |

---

## 9. Conflicts & risks to close (checklist)

- [ ] **Rewrite/annotate `PrivGuard_Project_Plan.md`** — its Rust-engine framing is the *research vision*,
      not the graded build. Mark it clearly or fold into a "Future Work" appendix so an examiner is not
      confused about what was actually built.
- [ ] **Retype schema for PostgreSQL** (`DOUBLE PRECISION`, `BYTEA`) and generate Django models.
- [ ] **Sandbox the SQL console** — parse with `sqlglot`, execute via ORM; never run analyst SQL as raw
      SQL. (This is both a correctness and a Section-5 security requirement.)
- [x] **Git** — private repo, `main` + `dev`, 50+ commits, PR workflow, collaborators added.
- [ ] **Confirm the users-table story** — `sys_principal` is the single Users table; be ready to say so.
- [ ] **Right-size scope for a 2–3 person team** — 28 tables is fine (they mostly mirror each other), but
      the DP features to *fully* implement are: contribution bounding, the ledger + trigger + transaction,
      DP aggregates, and memoization. Overlap detection and column encryption are the first to trim.
- [ ] **Every evaluation graph gets error bars** over ≥30 trials (research-plan checklist item; still
      applies and impresses examiners).

---

## 10. Decisions (locked 2026-07-25)

1. **Auth mechanism — DRF-JWT is the core.** Implement authentication with Django REST Framework +
   SimpleJWT (login, logout/blacklist, refresh, `/me`). Google OAuth 2.0 is a *time-permitting* add-on
   layered on the same user model (via `django-allauth` / `dj-rest-auth`), not a blocker for the core.
2. **User model — `sys_principal` IS the Django `AUTH_USER_MODEL`.** One custom user model backs both
   login and the DP principal; `pwd_hash` → Django's Argon2-hashed `password`, `principal_name` →
   `username`. No separate Django `User` table, no 1:1 bridge. Set `AUTH_USER_MODEL` before the first
   migration. Roles stay in `sys_role` + `sys_principal_role` (still the single-users-table pattern).
3. **Scope trim order — CP-ABE/encryption → predicate-overlap detection → snapping mechanism.** Drop in
   that order if time runs short. **Never cut:** contribution bounding, the budget ledger (+ trigger +
   transaction), the DP aggregate service, and noise memoization — these four *are* the project.
