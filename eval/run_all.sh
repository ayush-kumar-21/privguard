#!/usr/bin/env bash
# Reproduce every experiment in docs/EVALUATION.md. Needs only Docker.
#   ./eval/run_all.sh            # all experiments
# Figures are written to docs/eval/.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: stop it rewriting /repo paths
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v pwd >/dev/null && pwd -W >/dev/null 2>&1; then ROOT_MOUNT="$(cd "$ROOT" && pwd -W)"; else ROOT_MOUNT="$ROOT"; fi
IMG=python:3.12-slim
PIP="pip install -q -r //repo/backend/requirements.txt matplotlib 2>&1 | tail -1"

echo "== Experiments 1-2: collusion and partition selection (no database) =="
docker run --rm -v "$ROOT_MOUNT://repo" -w //repo/eval "$IMG" \
  bash -c "$PIP; python exp_collusion_and_partition.py"

echo "== Experiment 3: timing channel (seeded PostgreSQL) =="
docker network create pgeval >/dev/null 2>&1 || true
docker rm -f pgeval-db >/dev/null 2>&1 || true
docker run -d --name pgeval-db --network pgeval -e POSTGRES_DB=safehealth \
  -e POSTGRES_USER=privguard -e POSTGRES_PASSWORD=privguard postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec pgeval-db pg_isready -U privguard -d safehealth >/dev/null 2>&1 && break; sleep 1; done
docker run --rm --network pgeval -v "$ROOT_MOUNT://repo" -w //repo/backend \
  -e DATABASE_URL=postgres://privguard:privguard@pgeval-db:5432/safehealth -e SECRET_KEY=eval -e PRIVGUARD_SERVER_SECRET=eval-noise-secret -e DEBUG=False \
  -e ALLOWED_HOSTS=localhost,testserver -e CORS_ALLOWED_ORIGINS=http://localhost:5173 "$IMG" \
  bash -c "$PIP; python manage.py migrate -v0 && python manage.py seed_demo >/dev/null 2>&1; \
    python manage.py shell < ../eval/exp_timing.py 2>&1 | grep -v 'Insecure\|jwt\|return self\|decoded\|objects imported'; \
    echo; echo '== Experiment 4: direct database access =='; \
    python manage.py shell < ../eval/exp_direct_access.py 2>&1 | grep -v 'Insecure\|jwt\|return self\|decoded\|objects imported'"
docker rm -f pgeval-db >/dev/null; docker network rm pgeval >/dev/null
