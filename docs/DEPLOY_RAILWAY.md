# Deploying PrivGuard to Railway (for a 1 to 2 week live demo)

You are deploying the whole app on Railway: **PostgreSQL + the Django API + the React UI**, behind one
public URL. Budget about 45 minutes. Delete everything at the end (section 8).

The code is ready: branch `feat/railway-deploy`. Nothing here needs Docker on your laptop.

## 0. What you need
- The repo on GitHub (you have access) and a **Railway account**. Railway bills by usage; check their
  current trial credit or plan on their pricing page before you add a card.
- A terminal with `openssl` (macOS and Linux have it). Optional: the Railway CLI (`npm i -g @railway/cli`).
- You do the sign-ins, the payment details and the secret pasting yourself (section 9 explains why).

## 1. Make the secrets (on your laptop, never in the repo or in chat)
```bash
echo "SECRET_KEY=$(openssl rand -hex 32)"
echo "PRIVGUARD_SERVER_SECRET=$(openssl rand -hex 32)"
echo "PRIVGUARD_DEMO_PASSWORD=$(openssl rand -base64 18 | tr -d '=+/')"   # alice, frank, erin, carol
echo "PRIVGUARD_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '=+/')"  # dave, the administrator
```
Save them in a password manager. The demo accounts **do not use `privguard123` here**: that password is
public in the repo, and the seed command refuses to run in production without the two passwords above.
Share the passwords with the team by a private channel, not in a PR or an issue.

## 2. Create the project and the database
1. railway.com, **New Project**, **Deploy PostgreSQL**. Leave the service name `Postgres`.
2. In the project settings, set a **usage limit** (or alert) so a mistake cannot run up a bill.

## 3. Create the API service
1. **New**, **GitHub Repo**, pick `privguard`, then in the service settings:
   - **Service name:** `api` (the UI service looks for exactly this name)
   - **Branch:** `feat/railway-deploy` (or `main` once the PR is merged)
   - **Root directory:** `backend`  (Railway finds `backend/Dockerfile` itself)
   - **Networking:** do **not** generate a public domain for `api`; it is reached privately.
2. **Variables** (paste the secrets you made in step 1):

| Variable | Value |
|---|---|
| `DEBUG` | `False` |
| `SECRET_KEY` | from step 1 |
| `PRIVGUARD_SERVER_SECRET` | from step 1 |
| `DATABASE_URL` | the reference to the database's URL: `${{Postgres.DATABASE_URL}}` |
| `PORT` | `8000` |
| `ALLOWED_HOSTS` | `.railway.app,localhost` (leading dot = any subdomain; add your own domain if you attach one) |
| `PRIVGUARD_SEED_DEMO` | `1` |
| `PRIVGUARD_DEMO_PASSWORD` | from step 1 |
| `PRIVGUARD_ADMIN_PASSWORD` | from step 1 |
| `CSRF_TRUSTED_ORIGINS` | add in step 5, once you know the public URL |

3. Deploy. In the deploy log you should see the migrations run, `Principals: alice, frank, erin, carol, dave
   (passwords from the environment)`, then `Listening at: http://0.0.0.0:8000`.

## 4. Create the UI service
1. **New**, **GitHub Repo**, `privguard` again:
   - **Service name:** `ui`
   - **Branch:** same as above
   - **Root directory:** `frontend`
   - **Networking:** **Generate Domain**. This is the public URL you give out.
2. **Variables:**

| Variable | Value |
|---|---|
| `API_UPSTREAM` | `http://api.railway.internal:8000` (private address of the `api` service) |

   Do not set `PORT` on `ui`: Railway sets it and nginx listens on it.
3. Deploy. If the `ui` log says `host not found in upstream`, the `api` service was not up yet:
   redeploy `ui`.

## 5. Tell Django its public address
Copy the public URL of `ui` (for example `https://ui-production-xxxx.up.railway.app`) and add it on the
`api` service as `CSRF_TRUSTED_ORIGINS=https://ui-production-xxxx.up.railway.app`, then redeploy `api`.
Without this the Django admin login fails with a CSRF error.

## 6. Check it works (the live URL, in a browser)
- [ ] `/` shows the sign-in page, and sign-in works for `alice` with `PRIVGUARD_DEMO_PASSWORD`
- [ ] `alice` with `privguard123` is **refused** (this must fail)
- [ ] `alice` runs a query in the Console and gets a noisy answer; her remaining budget drops
- [ ] sign in as `dave` with `PRIVGUARD_ADMIN_PASSWORD`: the Users, Roles and Patient budgets pages open
- [ ] `/admin/` signs in as `dave`; then `/monitor/database/` shows the schema **and rows**
      (the no-login monitor key is switched off in production, so the monitor needs this sign-in)
- [ ] `/api/auth/register/` returns 404
- [ ] `https://<url>/api/health/` returns `{"status": "ok", ...}`

If the restricted analyst database role was not created (a note appears in the `api` deploy log from
migration `audit/0007`), tell the team. The app still works; that one defence-in-depth layer is off.

## 7. If something fails
| Symptom | Likely cause |
|---|---|
| `api` crashes at start, `ImproperlyConfigured ... SECRET_KEY` | `SECRET_KEY` or `PRIVGUARD_SERVER_SECRET` unset or a placeholder |
| `api` log: `CommandError: PRIVGUARD_DEMO_PASSWORD must be set...` | a password variable is missing or shorter than 12 characters |
| UI loads, API calls return 502 | `API_UPSTREAM` wrong, or the `api` service is not named `api` |
| `400 Bad Request` from the API | the public host is not covered by `ALLOWED_HOSTS` |
| Admin login: `CSRF verification failed` | `CSRF_TRUSTED_ORIGINS` missing or has a trailing slash |
| Page loads but is blank | open the browser console; usually a failed `/api/` call |

## 8. At the end of the 1 to 2 weeks
Put the date in your calendar now. Then: Railway, project, **Settings**, **Delete project** (this
removes the database too). Rotate nothing else: the secrets only ever existed for this deployment.

## 9. Using Claude in Chrome for this
It can help with the dashboard parts, but not all of it.

| Task | Claude in Chrome | You |
|---|---|---|
| Open Railway, find pages, click **New Project / Deploy PostgreSQL / New Service**, set branch and root directory | Yes | confirm each purchase-like step |
| Read the deploy logs and tell you what failed | Yes | |
| Run the section 6 checks on the live URL | Yes (sign-ins use your passwords: see below) | |
| **Sign in to Railway or GitHub** | **No** (it will not authenticate for you) | you |
| **Enter a card or payment details**, accept terms | **No** | you |
| **Paste secrets** into Railway's Variables (`SECRET_KEY`, passwords) | **No** (it never types secrets or passwords) | you |

Practical split: you sign in, add the card and paste the variables; Claude clicks through the rest and
verifies. For the secrets it is quicker and safer with the Railway CLI from your own terminal:
`railway login`, `railway link`, then `railway variables --service api --set "KEY=value"` for each row of
the table. The CLI needs you at the keyboard for the login only.

### Prompt to give Claude Code (with the Chrome extension) on your laptop
```
I am deploying PrivGuard to Railway. Read docs/DEPLOY_RAILWAY.md in this repo and follow it with me.
Rules: I sign in to Railway and GitHub myself and I add payment details myself. I will paste every
secret and password myself; never ask me to type one into this chat and never type one into a form for me.
Do the dashboard steps in Chrome (create the project and the Postgres service, the api and ui services,
branch and root directory, public domain), and stop to ask me before anything that costs money or is
hard to undo. Do not generate a public domain for the api service. After deploying, run section 6 of
the document against the live URL and report each box as passed or failed, with the evidence. If a
step does not match what you see on screen, say so instead of guessing, and tell me what you see.
```
