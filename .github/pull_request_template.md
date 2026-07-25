<!--
  All work merges into `dev` via PR. Only the repo owner promotes `dev` -> `main`.
  Direct pushes to `main` are blocked. Keep PRs small and focused.
-->

## What & why
<!-- One or two sentences: what this PR does and the reason. Link any issue. -->

Closes #

## Type
- [ ] feat
- [ ] fix
- [ ] refactor
- [ ] docs
- [ ] chore / infra

## Area
- [ ] backend (Django / DRF / privacy)
- [ ] database (models / migrations / SQL)
- [ ] frontend (React / UI)
- [ ] docs / project

## How I tested
<!-- Commands run, screens exercised, or the manual/automated checks. Paste output/screenshots. -->

## Checklist
- [ ] Branched from `dev` (not `main`), targeting `dev`.
- [ ] No secrets committed; `.env` is git-ignored.
- [ ] `python manage.py check` passes (backend changes).
- [ ] New migrations included and apply cleanly (schema changes).
- [ ] Meaningful commit messages.
- [ ] For UI: loading + empty + error states covered; light and dark verified; no em-dash in shipped strings.

## Notes for the reviewer (owner)
<!-- Anything specific you want the owner to look at before merging. -->
