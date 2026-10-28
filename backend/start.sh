#!/bin/sh
# Container start for hosted deployments (Railway, Render, ...). docker-compose runs its own sequence.
#
#   PORT                      the port to listen on (the platform sets it; default 8000)
#   PRIVGUARD_SEED_DEMO=1     seed the demo data and accounts once (idempotent; needs the two
#                             password variables when DEBUG is off, see seed_demo.py)
set -e
python manage.py migrate --noinput
if [ "${PRIVGUARD_SEED_DEMO:-0}" = "1" ]; then
  python manage.py seed_demo
  python manage.py refresh_catalog_stats || echo "refresh_catalog_stats failed (continuing)"
fi
python manage.py audit_separation_of_duty || echo "WARNING: separation-of-duty conflicts (see above)"
# One worker with threads: the monitor's request log lives in the process, so more workers would
# each show only their own share of the traffic.
exec gunicorn privguard.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers 1 --threads 8 --timeout 60
