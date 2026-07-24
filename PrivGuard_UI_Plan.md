# PrivGuard — Frontend / UI Plan

**Owner of this workstream:** UI teammate · **Stack already scaffolded:** React 18 + Vite + TypeScript + TailwindCSS (in `frontend/`).

This is the build spec for the PrivGuard web client. Follow it screen by screen. The
goal is a **precise, instrument-like, minimalist** interface — the feel of Linear /
Vercel / PlanetScale, not a templated admin theme. Read §1 (design system) fully
before writing any component; everything after it assumes those tokens.

> **Design read:** a developer / data-privacy **product UI (dashboard-class)** for
> technical analysts, auditors and DBAs, with a Linear/Vercel-style precise-minimalist
> language, built on **shadcn/ui + Radix primitives on Tailwind**, Geist + Geist Mono,
> Phosphor icons, restrained motion, **dark-first with full light parity**.
>
> **Dials:** VARIANCE 3 (structured, predictable app layout) · MOTION 3 (functional
> feedback only, no scroll theatrics) · DENSITY 6 (data-dense but breathing).

---

## 0. What we are NOT doing (anti-slop guardrails)

These are hard rules. A PR that violates them gets sent back.

- **No AI-purple / neon glows / mesh-gradient blobs.** One accent, used everywhere.
- **No pure black (`#000`) or pure white (`#fff`).** Off-black and off-white only (tokens below).
- **No default `Inter + slate-900` look.** Geist for UI, Geist Mono for all numbers/SQL/hashes.
- **No three identical feature cards, no decorative eyebrows, no fake data.** Use the seeded demo data.
- **No hand-rolled SVG icons.** Phosphor only, one weight.
- **One corner-radius scale, one accent, one theme per view.** No section inverts mid-page.
- **Every number is `font-mono` with `tabular-nums`.** Budgets, ρ, ε, counts, latencies, hashes.
- **Every list/table/panel ships loading, empty, and error states** — not just the happy path.
- **No em-dash characters in any shipped UI string.** Use a hyphen or restructure.

---

## 1. Design System (build this first, in `frontend/src/styles` + `tailwind.config.js`)

### 1.1 Foundation libraries to install

```bash
cd frontend
# component layer (owned code, Radix under the hood)
npx shadcn@latest init          # choose: TypeScript, Tailwind, CSS variables = yes
npx shadcn@latest add button input label select dialog dropdown-menu table \
  badge card tabs toast tooltip skeleton separator switch sonner
# fonts (self-hosted, no <link> to Google Fonts)
npm i @fontsource-variable/geist @fontsource-variable/geist-mono
# icons (one family, one weight)
npm i @phosphor-icons/react
# server state + tables + charts + editor + motion
npm i @tanstack/react-query @tanstack/react-table zustand
npm i @uiw/react-codemirror @codemirror/lang-sql   # SQL console editor (lighter than Monaco)
npm i motion                                        # import from "motion/react"
# recharts is already in package.json (budget + attack charts)
```

> shadcn components are **owned code** — never ship them in default state. Restyle radii,
> colors, and typography to the tokens below.

### 1.2 Design tokens — paste into `src/index.css`

Dark is the default; light is full parity. Tokens are CSS variables so shadcn and
charts read the same source of truth.

```css
@import "@fontsource-variable/geist";
@import "@fontsource-variable/geist-mono";

:root {
  /* neutrals (light) */
  --bg:            #FAFAFA;
  --surface:       #FFFFFF;
  --surface-2:     #F4F4F5;   /* elevated / hover */
  --border:        #E4E4E7;
  --border-strong: #D4D4D8;
  --text:          #18181B;
  --text-muted:    #52525B;
  --text-faint:    #A1A1AA;

  /* single brand/interactive accent (soft azure — NOT default indigo/purple) */
  --accent:        #3B6FE0;
  --accent-hover:  #315FC4;
  --accent-press:  #2A54AE;
  --accent-fg:     #FFFFFF;   /* text on accent (verify AA per use) */
  --accent-weak:   #EAF0FE;   /* tinted background for selected rows / info */

  /* semantic — privacy budget + status */
  --ok:      #1E9E6A;   /* budget healthy   */
  --warn:    #B4791A;   /* budget low       */
  --danger:  #D64545;   /* exhausted / rejected / tamper */
  --ok-weak:   #E4F6EE;
  --warn-weak: #FBF0DC;
  --danger-weak: #FBE7E7;

  /* radii (ONE scale): inputs 8, cards 10, pills full */
  --radius-sm: 8px;
  --radius:    10px;
  --radius-lg: 14px;

  --ring: var(--accent);
}

.dark {
  --bg:            #0B0B0F;
  --surface:       #131318;
  --surface-2:     #1B1B22;
  --border:        #26262E;
  --border-strong: #34343E;
  --text:          #F4F4F6;
  --text-muted:    #9B9BA6;
  --text-faint:    #6E6E78;

  --accent:        #5B8DEF;
  --accent-hover:  #6E9CF2;
  --accent-press:  #4B7BE0;
  --accent-fg:     #0B0B0F;
  --accent-weak:   #17233B;

  --ok:      #3DBE8B;
  --warn:    #E0A93A;
  --danger:  #EF5A5A;
  --ok-weak:   #10241C;
  --warn-weak: #2A2110;
  --danger-weak: #2A1414;
}

* { border-color: var(--border); }
body {
  background: var(--bg);
  color: var(--text);
  font-family: "Geist Variable", ui-sans-serif, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.mono, code, kbd, pre { font-family: "Geist Mono Variable", ui-monospace, monospace; }
.num { font-variant-numeric: tabular-nums; }        /* apply to every rendered number */
```

Map the variables in `tailwind.config.js` (`theme.extend.colors.bg`, `surface`, `accent`,
`ok`, `warn`, `danger`, etc.) so utilities like `bg-surface text-muted border-border`
work. Set `darkMode: "class"` and toggle `.dark` on `<html>`.

### 1.3 Type scale & rhythm

| Token | px | Use |
|---|---|---|
| display | 30 / 36 | page titles, login headline |
| h2 | 20 | section headers |
| h3 | 16 | card titles |
| body | 14 | default UI text (data app density) |
| small | 13 | table cells, secondary |
| micro | 12 | labels, captions, badges |

Headlines: `tracking-tight leading-tight`. Body: `leading-relaxed`, muted color.
Numbers/SQL/hashes: `.mono .num`. Spacing base 4px; card padding 16–20; page gutters
`max-w-[1400px] mx-auto px-6`.

### 1.4 Motion (MOTION 3 — subtle, motivated, reduced-motion honored)

Use `motion/react`. Allowed motions only, each justified: page/section fade+rise on
mount (120–180ms, ease `[0.16,1,0.3,1]`), list stagger on data load (≤ 60ms/item),
dialog scale-in, toast slide, budget meter fill transition, the `ρ cost` badge count-up
on EXPLAIN. Everything collapses to instant under `prefers-reduced-motion`. No
scroll-hijack, no parallax, no infinite loops.

---

## 2. App architecture

```
frontend/src/
  main.tsx                 # QueryClientProvider, RouterProvider, ThemeProvider
  index.css                # tokens (§1.2)
  lib/
    api.ts                 # axios instance + JWT interceptor + refresh (extend existing client.ts)
    queryKeys.ts           # centralized react-query keys
    format.ts              # rho/eps/number/date formatters (tabular)
  stores/
    auth.ts                # zustand: tokens, current principal, roles, login/logout
    ui.ts                  # theme, sidebar collapsed
  components/
    app-shell/             # Sidebar, Topbar, ThemeToggle, BudgetMiniMeter, UserMenu
    ui/                    # shadcn components (restyled)
    data-table/            # generic TanStack table (server pagination/sort/filter)
    charts/                # BudgetBurndown, AttackChart, PlanTree
    states/                # Skeletons, EmptyState, ErrorState, RoleGate
  routes/
    login.tsx
    dashboard.tsx
    console.tsx            # SQL console
    data/ patients.tsx visits.tsx diagnoses.tsx labs.tsx
    governance/ policies.tsx grants.tsx roles.tsx audit.tsx ledger.tsx
    attack-lab.tsx
```

- **Server state:** TanStack Query for every API read/mutation (caching, pagination,
  optimistic updates). Never store server data in Zustand.
- **Client state:** Zustand for auth session + theme + sidebar only.
- **Routing:** React Router. A protected layout route renders the app shell; `login`
  is public. Guard by auth, then by role (`<RoleGate roles={["dba"]}>`).

### 2.1 Auth flow (JWT / SimpleJWT — endpoints already built)

1. `POST /api/auth/login/ {username,password}` → `{access, refresh}`.
2. Store `access` in memory (Zustand) + `refresh` in `localStorage` (course-scope
   tradeoff; note the XSS caveat in the PR). Axios request interceptor attaches
   `Authorization: Bearer <access>`.
3. On `401`, response interceptor calls `POST /api/auth/refresh/`; on success retry,
   on failure clear session and redirect to `/login`.
4. `GET /api/auth/me/` on app load → hydrate principal + roles (drives nav + RoleGate).
5. Logout → `POST /api/auth/logout/` (blacklists refresh) + clear store.

---

## 3. App shell

```
┌───────────────────────────────────────────────────────────────────────────┐
│ ▌ PrivGuard          Overview / Console / Data / Governance / Attack Lab    │  ← Topbar 64px
│                                   [ρ 4.85 / 5.00 ▓▓▓▓▓░]   ☾   ● alice ▾    │
├──────────────┬────────────────────────────────────────────────────────────┤
│ Overview     │                                                             │
│ Console      │                   ROUTE CONTENT                             │
│ ─ Data       │                   (max-w-[1400px], px-6)                    │
│   Patients   │                                                             │
│   Visits     │                                                             │
│ ─ Governance │                                                             │
│   Policies   │                                                             │
│   Audit      │                                                             │
│ Attack Lab   │                                                             │
├──────────────┤                                                             │
│ ☾ theme  ⌘K  │                                                             │
└──────────────┴────────────────────────────────────────────────────────────┘
```

- **Left sidebar** (collapsible, 240px → 60px icon rail). Nav grouped: Overview,
  Console, Data (Patients, Visits, Diagnoses, Labs), Governance (Policies, Grants,
  Roles, Audit, Ledger), Attack Lab. **Role-filter the nav:** analyst sees
  Overview/Console/Attack Lab; clinician sees Data; dba sees everything; auditor sees
  Audit/Ledger. Active item: accent left-border + `bg-accent-weak`.
- **Topbar:** current section title, a **budget mini-meter** (ρ remaining as a thin
  bar that shifts `ok → warn → danger`), theme toggle, user menu (name, role chips,
  logout).
- Group with `border-b` / negative space, not boxes. No card-in-card.

---

## 4. Screens

Each screen lists: purpose, layout (ASCII), key components, API, and states.

### 4.1 Login  (`/login`) — the one landing-ish surface

Asymmetric split (skill hero rules apply): left brand panel, right form. Headline ≤ 2
lines, one primary CTA, no eyebrow clutter.

```
┌───────────────────────────┬───────────────────────────────┐
│                           │   Sign in                      │
│   PrivGuard               │   Username  [ alice        ]   │
│   Differentially private  │   Password  [ ••••••••     ]   │
│   query gateway.          │   [  Sign in  ]                │
│                           │   error text below input       │
│   (subtle static product  │                                │
│    mark / grid motif)     │                                │
└───────────────────────────┴───────────────────────────────┘
```

- Label **above** input; error **below**; never placeholder-as-label. Focus ring =
  accent. Primary button full-contrast (verify AA). Submit → §2.1.
- Left panel: quiet, no gradient blob. A restrained mono grid / the wordmark. On
  mobile, drop the left panel, stack the form. `min-h-[100dvh]`.

### 4.2 Dashboard  (`/`) — budget burn-down is the hero

```
┌────────────┬────────────┬────────────┬────────────┐
│ ρ remaining│ queries    │ cache-hit  │ suppressed │   ← stat tiles (mono, tabular)
│ 4.85 / 5.00│ today  23  │ rate  38%  │ groups  4  │
├────────────┴────────────┴────────────┴────────────┤
│  Budget burn-down                                  │
│  cumulative ρ (area) + equivalent ε (line)         │   ← BudgetBurndown chart
│  red exhaustion band at ρ_total                     │
├────────────────────────────────────────────────────┤
│  Recent queries                     [View audit →] │
│  status · principal · ρ · latency · time (table)   │
└────────────────────────────────────────────────────┘
```

- Stat tiles: plain layout (no heavy cards), big mono number + micro label. Color the
  ρ tile by remaining band.
- **BudgetBurndown** (Recharts area+line): x = time, y = cumulative ρ; overlay ε line
  on a secondary axis; a `--danger-weak` band above `rho_total`. **Load the `dataviz`
  skill before building this chart** (palette, axis, legend, tooltip discipline).
- API: `GET /api/dp/budget/`, `GET /api/dp/ledger/verify/`, `GET /api/queries/?ordering=-submitted_at`.
- States: skeleton tiles + chart shimmer on load; empty = "No queries yet. Open the
  Console." ; error = inline retry.

### 4.3 SQL Console  (`/console`) — the core analyst surface

```
┌──────────────────────────────────────────────┬───────────────┐
│  SELECT admission_type, COUNT(*)              │  ρ cost 0.05  │  ← live badge
│  FROM visits GROUP BY admission_type          │  Gaussian     │
│  (CodeMirror 6, SQL mode)                     │  [  Run  ]    │
├──────────────────────────────────────────────┴───────────────┤
│  status: executed   ρ charged 0.05   latency 42ms   [chip]    │
│  ┌──────────────┬──────────┐                                  │
│  │ admission_type│ value    │   results grid (mono numbers)   │
│  │ EMERGENCY     │ 242      │                                 │
│  └──────────────┴──────────┘   suppressed groups: 1           │
└───────────────────────────────────────────────────────────────┘
```

- Editor: `@uiw/react-codemirror` + `@codemirror/lang-sql`, themed to tokens. This is a
  code editor surface (skill §13) — use the library's skinning, do not hand-roll.
- **ρ cost badge:** show the noise mechanism + estimated ρ before running (for now,
  reflect the default 0.05; later wire an EXPLAIN-style preview). Count-up animation on
  change.
- Run → `POST /api/dp/query/ {db:"safehealth", sql, rho}`. Render `status` as a colored
  chip: `executed` (ok), `cache_hit` (accent), `rejected_budget` / `rejected_policy`
  (danger). Show `result`, `rho_charged`, `epsilon_charged`, `suppressed_groups`, and
  the updated budget mini-meter.
- Error (parse / policy): inline under the editor, quoting the API `error` string.
- Empty result set: "No rows (or all partitions suppressed)."

### 4.4 Data CRUD  (`/data/patients`, `/visits`, `/diagnoses`, `/labs`)

Generic `data-table/` component (TanStack Table, **server-side** pagination/sort/filter
against DRF), reused across all four.

```
┌───────────────────────────────────────────────────────────────┐
│  Patients          [search zip3/insurance] [sex ▾] [+ New]     │
├────────┬──────────┬──────┬──────┬───────────┬─────────────────┤
│ id     │ region   │ sex  │ age  │ insurance │ enrolled        │
│ 1042   │ North    │ F    │ 57   │ PPO       │ 2019-03-11      │
│ …                                                              │
├───────────────────────────────────────────────────────────────┤
│  ‹  1 2 3 … 8  ›     showing 1-20 of 150                        │
└───────────────────────────────────────────────────────────────┘
```

- Query params map straight to DRF: `?page=`, `?ordering=`, `?search=`, plus
  filter fields (`?sex=`, `?region=`, `?admission_type=` etc. per §5).
- Create/Edit in a shadcn `Dialog` with validated fields (label above, error below).
  Delete → confirm dialog. Mutations via TanStack Query, then invalidate the list key.
- **RBAC:** read for all authenticated; write controls (`+ New`, row edit/delete) render
  only for clinician/dba (mirror the API `403`). `mrn` is never displayed (access-mode
  `denied`).
- States: skeleton rows matching column widths; empty = "No patients match."; error row.

### 4.5 Governance — Policy Admin  (`/governance/policies`, `/grants`, `/roles`) — dba only

The showpiece "privacy is configuration" screen.

```
┌─────────────────────────────────────────────────────────────┐
│  Column access (column × role → mode)                        │
│              dp_analyst   clinician   auditor   dba           │
│ total_charge   [dp ▾]      [raw ▾]    [dp ▾]    [raw ▾]       │
│ mrn            [denied ▾]  [raw ▾]    [denied▾] [raw ▾]       │
├─────────────────────────────────────────────────────────────┤
│  Column privacy (mechanism · clamp · min-partition)          │
│ total_charge  gaussian  [0]—[50000]  min 5   [Save]          │
├─────────────────────────────────────────────────────────────┤
│  Table privacy (privacy unit · max rows · max groups)        │
│ visits  unit=patient_id  maxRows 5  maxGroups 3  [Save]      │
└─────────────────────────────────────────────────────────────┘
```

- Access-mode dropdowns write `PATCH /api/column-grants/{id}/`. Column-privacy editor
  → `/api/column-policies/`. Table-privacy → `/api/table-policies/`.
- `RoleGate roles={["dba"]}`; non-dba never see this route (nav hidden + route guard).
- Optimistic update on the grid with rollback on error toast.

### 4.6 Governance — Audit & Ledger  (`/governance/audit`, `/ledger`)

- **Audit:** queries data-table (`/api/queries/`), filter by `status`, search `sql_text`,
  sort by `submitted_at` / `rho_charged` / `latency_ms`. Status chips as in §4.3. Scoped
  server-side to the caller unless dba/auditor.
- **Ledger:** a "Verify integrity" panel → `GET /api/dp/ledger/verify/` → render
  `ok` (green shield / red broken-shield via Phosphor), `entries`, `first_bad_entry`.
  Below it, the budget accounts view (`/api/budget-accounts/`) as read-only rows with
  ρ meters. This visualizes the hash-chained, tamper-evident ledger.

### 4.7 Attack Lab  (`/attack-lab`) — the demo moment

Two panels, each a button that runs a scripted sequence and charts the result.

```
┌───────────────────────────────┬───────────────────────────────┐
│  Averaging attack             │  Differencing attack          │
│  Run the same query N times.  │  Two overlapping predicates.  │
│  [ Run 200× ]                 │  [ Run ]                      │
│                               │                               │
│  error vs #queries            │  detection: allow/surcharge/  │
│  (line: memoization ON vs OFF)│  block  +  Δ estimate         │
└───────────────────────────────┴───────────────────────────────┘
```

- Averaging: fire `POST /api/dp/query/` repeatedly (same SQL). With memoization on, the
  cache returns identical answers at ρ=0 — plot estimation error flat vs. the naive
  "noise averages away" curve. (For the "OFF" comparison, either a backend `?nocache=1`
  flag the DB teammate can add, or simulate client-side and label it as such.)
- Differencing: submit two near-identical predicates; surface the API status /
  `predicate_overlap` action.
- Charts via Recharts (load `dataviz` skill first). Buttons show a progress/loading
  state during the run; results animate in.

---

## 5. API integration map (all under `/api`, JWT required except login/register)

| Screen | Method + endpoint | Notes |
|---|---|---|
| Login | `POST auth/login` `auth/refresh` `auth/logout` `auth/me` | §2.1 |
| Dashboard | `GET dp/budget` · `GET dp/ledger/verify` · `GET queries?ordering=-submitted_at` | burn-down + tiles |
| Console | `POST dp/query` | `{db,sql,rho}` → noisy result + budget |
| Data CRUD | `GET/POST/PATCH/DELETE patients\|visits\|diagnoses\|lab-results` | server pagination/search/filter |
| Policies | `GET/PATCH column-grants` · `column-policies` · `table-policies` | dba only |
| Audit | `GET queries?status=&search=&ordering=` | scoped by role |
| Ledger | `GET dp/ledger/verify` · `GET budget-accounts` | integrity + meters |
| Attack Lab | `POST dp/query` (repeated) | scripted sequences |

Filter/search params supported by the backend today: `patients` → `?sex= ?region=
?insurance_type= ?search=zip3,insurance`; `visits` → `?dept= ?admission_type= ?patient=
?ordering=admit_ts,total_charge`; `queries` → `?status= ?search=sql_text
?ordering=submitted_at`. Pagination is `?page=` (page size 20). Ordering via `?ordering=`.

---

## 6. Shared components to build once

- **StatusChip** — maps DP status → color + label (executed/cache_hit/rejected_*).
- **RhoMeter / BudgetMiniMeter** — thin bar, `ok → warn → danger` by remaining fraction.
- **RhoBadge** — mono ρ value + mechanism, count-up on change.
- **DataTable** — generic TanStack table: columns def, server pagination, sort, search,
  row actions, skeleton + empty + error built in.
- **RoleGate** — renders children only if `me.roles ∩ allowed`.
- **states/** — `Skeleton*` matching each layout, `EmptyState` (icon + line + optional
  action), `ErrorState` (message + retry).
- **ThemeToggle** — toggles `.dark` on `<html>`, persists to `localStorage`.

---

## 7. Accessibility & quality bar (Definition of Done)

- WCAG AA contrast for text, buttons, inputs, focus rings — in **both** themes.
- Full keyboard nav; visible accent focus ring on every interactive element.
- `prefers-reduced-motion` collapses all motion to instant.
- Every screen ships loading + empty + error states.
- All numbers `mono` + `tabular-nums`; SQL and hashes `mono`.
- Responsive: sidebar collapses to a drawer < 768px; tables scroll inside
  `overflow-x-auto`; page body never scrolls horizontally.
- No em-dash in any shipped string. No console errors. Lighthouse pass on the dashboard.

---

## 8. Suggested build order (milestones for the UI teammate)

1. **Foundation** — tokens (§1.2), Tailwind mapping, shadcn init, fonts, theme toggle, app shell (§3), auth store + interceptor + protected routing.
2. **Login** (§4.1) end-to-end against `auth/*`.
3. **Dashboard** (§4.2) — stat tiles + BudgetBurndown (load `dataviz` skill) + recent queries.
4. **SQL Console** (§4.3) — editor + Run + status/result + budget refresh.
5. **DataTable + Data CRUD** (§4.4) — patients first, then reuse for visits/diagnoses/labs.
6. **Governance** (§4.5–4.6) — policy grid, audit table, ledger verify (dba/auditor gated).
7. **Attack Lab** (§4.7).
8. **Polish** — states, reduced-motion, responsive, a11y pass, Lighthouse.

Each milestone is a `feature/ui-<name>` branch → PR into `dev` (see `CONTRIBUTING.md`).

---

## 9. Coordination with the database teammate

- The UI depends only on the documented `/api` contract (§5). If a screen needs a new
  field/filter/endpoint (e.g. an EXPLAIN-preview for the ρ badge, or a `?nocache=1`
  flag for the Attack Lab OFF-curve), open an issue and let the DB teammate add it
  server-side — do **not** work around it with client hacks.
- Run the backend with seeded data: `python manage.py migrate && python manage.py
  seed_demo` then `runserver`. Log in as `alice` / `privguard123` (analyst) or `dave`
  (dba) to exercise role-gated views.
