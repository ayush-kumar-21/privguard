# Rubric completion plan (BCSE307L — 30 + 3 marks)

Status of every graded item, what was done in code, and the few steps that need the team's own
accounts. Update the boxes as you go; this file is the single checklist for submission day.

Legend: ✅ done and verified · 🟡 done, needs a visual check · ⏳ needs a team member's account

## 1. Problem statement & planning (2)

- ✅ Proposal with all 8 items — `PrivGuard_Project_Proposal.md`
- ✅ Gantt chart (Mermaid) in the proposal
- ⏳ Fill in real team member names/roles in the proposal if still placeholders

## 2. Database design (6)

- ✅ ER diagrams as images — `docs/er/er-catalog-privacy.png`, `docs/er/er-safehealth.png`
  (Mermaid sources beside them; regenerate with `mmdc -i <file>.mmd -o <file>.png -s 2 -b white`)
- ✅ 28 entities / 39 relationships / 28 tables, PK + FK + CHECK/UNIQUE/DEFAULT, 3NF, 39 indexes
- ✅ Data dictionary — `PrivGuard_Data_Dictionary.md`
- ✅ `staff` vs users-table question answered in `PrivGuard_ER_Documentation.md` §4

## 3. Database implementation (8)

- ✅ PostgreSQL + Django ORM; raw SQL only for procedures/functions
- ✅ 4 views (`v_budget_status`, `v_query_audit`, `v_visit_summary`, `v_ledger_reconciliation`) —
  now covered by `tests/test_constraints.py::TestViews`
- ✅ Stored procedures `sp_reserve_budget` / `sp_settle_budget` / `sp_refill_budget`
- ✅ Triggers `trg_ledger_hash` / `trg_ledger_readonly` / `trg_ledger_no_truncate`
- ✅ Transactions (`transaction.atomic`, procedures)
- ✅ CRUD, joins, aggregates through the ORM

## 4. Application development (4)

- ✅ Login, dashboard, CRUD screens, role-gated menu
- ✅ API search / filter / ordering / pagination — covered by `tests/test_api.py::TestListFeatures`
- ✅ Form validation: required, numeric min/max, max length, inline per-field errors
  (`ResourceForm.tsx`); limits mirror DB CHECK constraints (severity 1–5, birth year 1900–2100)
- ✅ Exception handling: uniform JSON error envelope (`backend/api/exceptions.py`),
  client `apiError` understands it, React `ErrorBoundary` for render crashes
- 🟡 Responsive UI: Tailwind breakpoints are used in 13 files — open the app at phone width once and
  screenshot it for the report

## 5. Authentication, authorization & security (5)

- ✅ JWT login / refresh / logout (blacklist) / `/me` — `tests/test_api.py::TestAuthentication`
- ✅ Single users table (`sys_principal`), 4 roles, API RBAC (`api/permissions.py`),
  menu/page RBAC (`RoleGate.tsx`) — `TestSingleUsersTable`, `TestRoleBasedAccess`
- ✅ Argon2 hashing (asserted in a test), ORM-parameterised queries, secrets in `.env`
  (git-ignored; `.env.example` committed)
- Optional: Google OAuth. The brief lists JWT as an accepted mechanism, so this is not required.

## 6. Professional practices (5)

- ✅ Private repo, collaborators added, `main` + `dev`, 50+ commits, PRs, `.gitignore`, README
- ✅ Backup/restore — `db/backup.sh`, `restore.sh`, `verify_restore.sh` (pg_dump / pg_restore)
- ✅ Docker — three Dockerfiles + `docker-compose.yml`; both images build (checked locally and by the
  new `Docker images build` CI job)
- ⏳ **Docker Hub** — workflow `docker-publish.yml` is ready. To activate it:
  1. Create a Docker Hub access token (Account settings → Security → New access token).
  2. In GitHub: Settings → Secrets and variables → Actions → add `DOCKERHUB_USERNAME` and
     `DOCKERHUB_TOKEN`.
  3. Push to `main` (or run the workflow manually). Images appear as
     `<user>/privguard-api` and `<user>/privguard-ui`.
  4. Put the two names in the README "Links" table.
- ⏳ **Cloud deployment + live URL** — follow `DEPLOYMENT.md` (Render blueprint `render.yaml`). Put the
  URL in the README "Links" table. Do a login on the live site before submitting.

## Bonus (+3)

- ✅ +1 CI/CD — GitHub Actions: oracle, Rust, Django (migrations check, seed, ledger verification,
  pytest), **Frontend build**, **Docker images build**, Qodana
- ✅ +1 Best practice — audit logging (`sys_query` + hash-chained ledger); also automated testing
- ⏳ +1 Custom domain — free option: DuckDNS subdomain → CNAME to the Render service, then add it as a
  custom domain in Render (Settings → Custom Domains). Mention it in the README.

## Pre-submission checklist

- [ ] Merge this branch into `main`; fast-forward `dev` to `main` (`dev` is currently behind) and
      keep using feature branches → PR → `dev` → `main`
- [ ] CI green on `main`
- [ ] Docker Hub secrets added; `Publish Docker images` run succeeded
- [ ] Deployed; live URL + Docker Hub names filled in README
- [ ] Run the backup → restore drill once and keep the terminal output for the viva
- [ ] Screenshot: ER diagrams, dashboard, SQL console, audit log, phone-width layout
- [ ] Viva prep: be ready to explain the ledger trigger, the reserve/settle procedures, why one
      users table, and why analyst SQL is parsed (sqlglot) and never executed as raw SQL
