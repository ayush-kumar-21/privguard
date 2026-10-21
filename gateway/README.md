# PrivGuard gateway

FastAPI in front of the engine: JWT auth (python-jose, HS256), argon2id password hashes, and
the query, budget, ledger and audit endpoints the React UI calls. It never touches data; it
hands SQL text to an `EngineClient` (`privguard_gateway/engine.py`).

Phase 2 ships `StubEngine`: canned answers, but real budget charging, per-principal cache hits,
`SPEC.md` §9 error classes and an audit log. Phase 3 swaps in a psycopg client speaking pgwire
to the Rust engine.

Paths, trailing slashes, the pagination envelope and the `{error: {code, message, details}}`
envelope match the legacy Django API, so the existing UI works against either.

## Run

```bash
cd gateway
export PRIVGUARD_GATEWAY_SECRET="$(openssl rand -base64 48)"   # required, >= 32 chars
export PRIVGUARD_GATEWAY_DEMO_USERS=1                          # alice/frank/erin/carol/dave
uv run --python 3.12 uvicorn privguard_gateway.app:create_app --factory --port 8001
```

The demo password is `privguard123`, as in the legacy seed. There is no built-in signing
secret: the gateway refuses to start without one.

Point the UI at it with `cd frontend && npm run dev:gateway` (reads `frontend/.env.gateway`).
The SafeHealth data pages and the role/grant/policy editors exist only in Django and are hidden
in that mode.

## Test

```bash
./run.sh gateway        # or: cd gateway && uv run --extra dev pytest
```

## Configuration

| Variable | Meaning |
|---|---|
| `PRIVGUARD_GATEWAY_SECRET` | JWT signing secret. Required, at least 32 characters. |
| `PRIVGUARD_GATEWAY_DEMO_USERS` | `1` seeds the five demo principals. Off by default. |
| `PRIVGUARD_GATEWAY_CORS` | Comma-separated browser origins allowed to call the API. |

## Not here yet

Principals live in memory (Phase 3 moves them to the engine's `sys_principal`), there is no
login rate limiting, and the ledger verdict is the stub's. Role-based column checks arrive with
the binder (4.1).
