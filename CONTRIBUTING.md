# Contributing to PrivGuard

A team of 2–3. One person owns the repo and reviews everything that reaches `main`.
This guide is the working agreement: how we branch, commit, and merge.

## Roles
- **Owner / reviewer** — reviews Pull Requests and is the only person who merges into
  `main`.
- **Contributors** — UI teammate and database teammate. Work on `dev` / feature
  branches and open PRs.

## Branch model
- `main` — protected. Always deployable. **No direct commits or pushes.** Only updated
  by the owner merging a reviewed PR.
- `dev` — integration branch. All feature work lands here first.
- `feature/<area>-<short-name>` — your working branches, cut from `dev`
  (e.g. `feature/ui-dashboard`, `feature/db-audit-index`).

```
feature/ui-dashboard ─┐
feature/db-audit ─────┼──PR──▶ dev ──PR (owner review)──▶ main
                      ┘
```

## Day-to-day flow (contributors)
```bash
git switch dev && git pull
git switch -c feature/ui-dashboard      # branch from dev
# ... work, commit ...
git push -u origin feature/ui-dashboard
# open a Pull Request into `dev` on GitHub (the PR template loads automatically)
```
- Keep PRs small and focused. Fill in the template. Tag the owner as reviewer.
- The owner reviews, requests changes if needed, and merges into `dev`.

## Promoting to main (owner only)
When `dev` is stable, the owner opens a PR `dev -> main`, reviews the diff, and merges.
This is the single gate to `main`.

## Local protection (works now, before the repo is on GitHub)
A pre-commit hook blocks direct commits to `main`. Enable it once per clone:
```bash
git config core.hooksPath .githooks
```
It rejects `git commit` while on `main` (merge commits are still allowed, so the owner
can merge locally). This is advisory — the real enforcement is GitHub branch protection
below.

## GitHub branch protection (owner, after pushing to GitHub)
1. Replace `@OWNER` in `.github/CODEOWNERS` with your GitHub username, commit, push.
2. Create the private repo and push both branches:
   ```bash
   gh repo create privguard --private --source . --push
   git push -u origin main dev
   ```
3. Protect `main` so nothing merges without your review:
   ```bash
   gh api -X PUT "repos/OWNER/privguard/branches/main/protection" --input - <<'JSON'
   {
     "required_pull_request_reviews": {
       "require_code_owner_reviews": true,
       "required_approving_review_count": 1
     },
     "required_status_checks": null,
     "enforce_admins": false,
     "restrictions": null,
     "allow_force_pushes": false,
     "allow_deletions": false
   }
   JSON
   ```
   (Or GitHub UI: **Settings → Branches → Add branch ruleset** → target `main` →
   require a pull request + require review from Code Owners + block force pushes.)
4. Add teammates: **Settings → Collaborators** (or `gh repo add-collaborator`), give
   them `write` (not `admin`), so they can push feature branches but not bypass the rule.

## Commit messages (Conventional Commits)
`type(scope): summary` — e.g. `feat(api): add EXPLAIN preview`, `fix(ui): dark focus ring`,
`docs: update README`. Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`.
Aim for meaningful, incremental commits (the rubric wants progressive history).

## Before you open a PR
- `python manage.py check` passes (backend).
- New migrations included and apply cleanly (schema changes).
- No secrets committed (`.env` is git-ignored).
- UI: loading/empty/error states, light + dark verified, no em-dash in shipped strings.
