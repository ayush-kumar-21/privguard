# PrivGuard gateway

FastAPI in front of the engine: JWT auth (python-jose, HS256), argon2id password hashes, and
the query, budget, ledger and audit endpoints the React UI calls. It never touches data; it
hands SQL text to an `EngineClient` (`privguard_gateway/engine.py`).

Two engines sit behind that seam. `StubEngine` returns canned answers (with real budget charging,
per-principal cache hits, `SPEC.md` §9 error classes and an audit log), and is the default so the
gateway runs with nothing else installed. `PgEngine` (`pgengine.py`) is the real thing: psycopg 3
over the Postgres wire protocol to `privguardd`. Set `PRIVGUARD_ENGINE_ADDR` to use it.

Paths, trailing slashes, the pagination envelope and the `{error: {code, message, details}}`
envelope match the legacy Django API, so the existing UI works against either.

## Run against the real engine

```bash
# 1. a database: load the dataset (see ./run.sh data), then add the demo principals
export PRIVGUARD_SERVER_SECRET="$(openssl rand -base64 32)"   # keys the noise
export PRIVGUARD_ENGINE_SECRET="$(openssl rand -base64 32)"   # the gateway's password to the engine
cd engine && cargo build -p privguardd
target/debug/privguardd load ../data/safehealth /tmp/pg-db
target/debug/privguardd seed-demo /tmp/pg-db
target/debug/privguardd serve /tmp/pg-db            # listens on 127.0.0.1:5433

# 2. the gateway, pointed at it (same engine secret)
cd ../gateway
export PRIVGUARD_ENGINE_ADDR=127.0.0.1:5433
uv run --python 3.12 uvicorn privguard_gateway.app:create_app --factory --port 8001
```

Set `PRIVGUARD_COLUMN_KEY` (64 hex digits) when running `privguardd load` to store
`patients.mrn` AES-256-GCM encrypted; the engine needs the same variable to read it.

## Run (stub engine)

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
| `PRIVGUARD_ENGINE_ADDR` | `host:port` of the engine's pgwire listener. Unset = stub engine. |
| `PRIVGUARD_ENGINE_SECRET` | The password the gateway presents to the engine (at least 32 characters here, 16 on the engine). Required with `PRIVGUARD_ENGINE_ADDR`. |

## Not here yet

Gateway principals (passwords, JWT) live in memory; the engine keeps its own principal, role and
grant tables, and the two must name the same users. There is no login rate limiting. Latency is
not recorded by the engine, so `latency_ms` is `null` for auditors.
