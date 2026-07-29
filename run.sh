#!/usr/bin/env bash
#
# PrivGuard: one command to bring the whole project up.
#
#   ./run.sh            start db + api + ui, wait until healthy, print URLs
#   ./run.sh dev        start db + api, then the Vite dev server with hot reload
#   ./run.sh stop       stop the containers, keep the database
#   ./run.sh restart    stop then start
#   ./run.sh rebuild    rebuild the images from scratch and start
#   ./run.sh logs [svc] follow logs, optionally for one service
#   ./run.sh status     show what is running and on which ports
#   ./run.sh reset      DESTROY the database volume and start clean
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# --- output helpers ----------------------------------------------------------
if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
  BOLD=''; DIM=''; RED=''; GREEN=''; YELLOW=''; RESET=''
fi
say()  { printf '%s\n' "$*"; }
info() { printf '%s\n' "${DIM}$*${RESET}"; }
ok()   { printf '%s\n' "${GREEN}$*${RESET}"; }
warn() { printf '%s\n' "${YELLOW}$*${RESET}" >&2; }
die()  { printf '%s\n' "${RED}$*${RESET}" >&2; exit 1; }

# --- compose v2 with a v1 fallback -------------------------------------------
if docker compose version >/dev/null 2>&1; then
  compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
  compose() { docker-compose "$@"; }
else
  die "Docker Compose was not found. Install Docker Desktop: https://docs.docker.com/get-docker/"
fi

# --- the daemon --------------------------------------------------------------
# On Windows and macOS the CLI is installed but the daemon only runs while
# Docker Desktop is open, so offer to start it rather than just failing.
start_docker_desktop() {
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      local exe="/c/Program Files/Docker/Docker/Docker Desktop.exe"
      [ -f "$exe" ] || return 1
      info "Starting Docker Desktop..."
      powershell.exe -NoProfile -Command \
        'Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"' >/dev/null 2>&1 || return 1
      ;;
    Darwin)
      [ -d "/Applications/Docker.app" ] || return 1
      info "Starting Docker Desktop..."
      open -g -a Docker || return 1
      ;;
    *) return 1 ;;
  esac
}

require_docker() {
  docker info >/dev/null 2>&1 && return 0

  start_docker_desktop || die "The Docker daemon is not running. Start Docker and try again."

  printf '%s' "${DIM}Waiting for the Docker daemon${RESET}"
  for _ in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then printf '\n'; ok "Docker is ready."; return 0; fi
    printf '.'; sleep 5
  done
  printf '\n'
  die "The Docker daemon did not come up within 5 minutes."
}

# --- port discovery ----------------------------------------------------------
# Never hardcode host ports. docker-compose.override.yml exists precisely so a
# developer can remap them around whatever else is bound on their machine, so
# ask Compose where a service actually landed.
published_port() {
  compose port "$1" "$2" 2>/dev/null | sed -n 's/.*:\([0-9][0-9]*\)$/\1/p' | head -1
}

api_port() { published_port api 8000; }
ui_port()  { published_port ui 80; }
db_port()  { published_port db 5432; }

wait_for_api() {
  local port="$1"
  printf '%s' "${DIM}Waiting for the API on :${port}${RESET}"
  for _ in $(seq 1 60); do
    # Any HTTP response means Django is serving. /auth/me/ answers 401 without
    # a token, which is a perfectly good readiness signal.
    if [ "$(curl -s -o /dev/null -m 3 -w '%{http_code}' \
            "http://localhost:${port}/api/auth/me/" 2>/dev/null)" != "000" ]; then
      printf '\n'; return 0
    fi
    printf '.'; sleep 2
  done
  printf '\n'
  warn "The API did not answer in time. Check: ./run.sh logs api"
  return 1
}

# --- dev-server wiring -------------------------------------------------------
# The browser talks to a relative /api in dev exactly as it does behind nginx in
# production, so Vite proxies it. That proxy needs the API's real host port.
# Rewrite only our own key and leave any other line in the file alone.
sync_frontend_env() {
  local port="$1" file="frontend/.env" target="http://localhost:$1"
  local line="API_PROXY_TARGET=${target}"

  if [ -f "$file" ] && grep -qxF "$line" "$file"; then
    return 0
  fi

  local tmp
  tmp="$(mktemp)"
  # Any other key the developer put here is kept, and kept first: an existing
  # file may open with a byte-order mark, which dotenv ignores at position 0
  # but would read as part of a key name anywhere else.
  if [ -f "$file" ]; then
    grep -v '^[[:space:]]*API_PROXY_TARGET=' "$file" \
      | grep -v '^# Written by run\.' > "$tmp" || true
  fi
  {
    say "# Written by run.sh: Vite proxies /api to the port Compose published."
    say "$line"
  } >> "$tmp"
  mv "$tmp" "$file"
  info "Wrote ${file} (API_PROXY_TARGET=${target})"
}

# --- summary -----------------------------------------------------------------
print_urls() {
  local ui api db
  ui="$(ui_port)"; api="$(api_port)"; db="$(db_port)"
  say ""
  ok "${BOLD}PrivGuard is up.${RESET}"
  say ""
  [ -n "$ui" ]  && say "  App          ${BOLD}http://localhost:${ui}${RESET}"
  [ -n "$api" ] && say "  API          http://localhost:${api}/api/"
  [ -n "$db" ]  && say "  Postgres     localhost:${db}  ${DIM}(privguard / privguard / safehealth)${RESET}"
  say ""
  say "  Sign in with any seeded principal, password ${BOLD}privguard123${RESET}:"
  say "    ${DIM}alice${RESET}  analyst    full budget, populated burn-down"
  say "    ${DIM}frank${RESET}  analyst    nearly exhausted budget"
  say "    ${DIM}dave${RESET}   dba        every screen, policy administration"
  say "    ${DIM}erin${RESET}   auditor    audit log and ledger"
  say ""
  info "  ./run.sh dev     hot-reloading dev server"
  info "  ./run.sh logs    follow the logs"
  info "  ./run.sh stop    stop everything"
  say ""
}

# --- commands ----------------------------------------------------------------
cmd_up() {
  require_docker
  info "Building and starting containers..."
  compose up -d "$@"
  local port; port="$(api_port)"
  [ -n "$port" ] || die "The api service did not publish a port. Check: ./run.sh logs api"
  wait_for_api "$port" || true
  sync_frontend_env "$port"
  print_urls
}

cmd_dev() {
  require_docker
  info "Starting db + api..."
  compose up -d db api
  local port; port="$(api_port)"
  [ -n "$port" ] || die "The api service did not publish a port. Check: ./run.sh logs api"
  wait_for_api "$port" || true
  sync_frontend_env "$port"

  [ -d frontend/node_modules ] || { info "Installing frontend dependencies..."; (cd frontend && npm install); }

  say ""
  # Vite wants 5173 but steps to the next free port if something already holds
  # it, so let it announce its own URL rather than promising one here.
  ok "${BOLD}Starting the dev server. Vite prints its URL below.${RESET}"
  info "API proxied to http://localhost:${port}. Ctrl+C stops the dev server; containers keep running."
  say ""
  cd frontend && exec npm run dev
}

cmd_reset() {
  require_docker
  warn "This deletes the 'pgdata' volume. Every row in Postgres is destroyed and"
  warn "the database is rebuilt from migrations plus seed_demo."
  printf '%s' "Type 'yes' to continue: "
  read -r reply
  [ "$reply" = "yes" ] || { say "Cancelled."; exit 0; }
  compose down -v
  cmd_up
}

case "${1:-up}" in
  up|start|"") cmd_up ;;
  rebuild)     require_docker; compose build --no-cache; cmd_up --force-recreate ;;
  dev)         cmd_dev ;;
  stop|down)   require_docker; compose stop; ok "Stopped. Data is kept; ./run.sh to start again." ;;
  restart)     require_docker; compose stop; cmd_up ;;
  logs)        require_docker; shift || true; compose logs -f --tail 100 "$@" ;;
  status)      require_docker; compose ps ;;
  reset)       cmd_reset ;;
  -h|--help|help)
    sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;
  *) die "Unknown command '$1'. Try: ./run.sh help" ;;
esac
