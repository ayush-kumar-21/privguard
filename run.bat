@echo off
rem PrivGuard: one command to bring the whole project up.
rem
rem  App stack (Docker)
rem   run.bat            build (with retries) then start db + api + ui, print URLs
rem   run.bat build      build the images only, do not start anything
rem   run.bat dev        start db + api, then the Vite dev server with hot reload
rem   run.bat stop       stop the containers, keep the database
rem   run.bat restart    stop then start
rem   run.bat rebuild    rebuild the images from scratch and start
rem   run.bat logs [svc] follow logs, optionally for one service
rem   run.bat status     show what is running and on which ports
rem   run.bat reset      DESTROY the database volume and start clean
rem
rem  Blueprint build (see IMPLEMENTATION_PLAN.md)
rem   run.bat engine     build and test the Rust engine in Docker
rem   run.bat oracle     run the Python DP oracle test suite
rem   run.bat data       regenerate the SafeHealth dataset
rem   run.bat check      oracle + engine; everything that needs no container

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "CMD=%~1"
if "%CMD%"=="" set "CMD=up"

if /i "%CMD%"=="help"   goto :help
if /i "%CMD%"=="-h"     goto :help
if /i "%CMD%"=="--help" goto :help

rem The oracle and the dataset generator are plain Python via uv -- no Docker,
rem so dispatch them before the daemon check rather than making someone start
rem Docker Desktop to run a unit test.
if /i "%CMD%"=="oracle"  goto :oracle
if /i "%CMD%"=="data"    goto :data

call :require_docker || exit /b 1
call :ensure_env || exit /b 1

if /i "%CMD%"=="up"      goto :up
if /i "%CMD%"=="start"   goto :up
if /i "%CMD%"=="build"   goto :build
if /i "%CMD%"=="engine"  goto :engine
if /i "%CMD%"=="check"   goto :check
if /i "%CMD%"=="dev"     goto :dev
if /i "%CMD%"=="stop"    goto :stop
if /i "%CMD%"=="down"    goto :stop
if /i "%CMD%"=="restart" goto :restart
if /i "%CMD%"=="rebuild" goto :rebuild
if /i "%CMD%"=="logs"    goto :logs
if /i "%CMD%"=="status"  goto :status
if /i "%CMD%"=="reset"   goto :reset

echo Unknown command "%CMD%". Try: run.bat help
exit /b 1

rem ---------------------------------------------------------------- commands
:up
echo Building images. The first run pulls base images and installs npm/pip
echo packages, so it can take a few minutes.
call :build_with_retry "" || exit /b 1
echo Starting containers...
docker compose up -d %2 %3 %4 || exit /b 1
call :api_port
if "!API_PORT!"=="" (
  echo The api service did not publish a port. Check: run.bat logs api
  exit /b 1
)
call :wait_for_api !API_PORT!
call :sync_env !API_PORT!
call :print_urls
exit /b 0

:rebuild
echo Rebuilding images from scratch...
docker compose build --no-cache || exit /b 1
docker compose up -d --force-recreate || exit /b 1
call :api_port
call :wait_for_api !API_PORT!
call :sync_env !API_PORT!
call :print_urls
exit /b 0

:build
echo Building images...
call :build_with_retry "" || exit /b 1
echo Done. Run "run.bat" to start the containers.
exit /b 0

rem ------------------------------------------------- engine, oracle, dataset
rem The Rust engine builds and tests inside Docker: no host toolchain needed,
rem and the pinned base image means a build here matches CI.
:engine
echo Building the Rust engine...
docker build -t privguard-engine engine || exit /b 1
echo.
echo Running the engine test suite...
docker run --rm privguard-engine || exit /b 1
exit /b 0

rem The oracle is the Python reference implementation the engine is validated
rem against. uv builds a throwaway venv, so there is nothing to install first.
:oracle
call :require_uv || exit /b 1
echo Running the DP oracle test suite...
pushd oracle
uv run --with pytest --python 3.12 pytest %2 %3 %4
set "RC=!errorlevel!"
popd
exit /b !RC!

:data
call :require_uv || exit /b 1
echo Regenerating the SafeHealth dataset...
pushd oracle
uv run --python 3.12 python -m privguard_oracle.dataset --out ../data/safehealth
set "RC=!errorlevel!"
popd
exit /b !RC!

rem Everything that can fail without starting a container.
:check
call :require_uv || exit /b 1
echo === DP oracle ===
pushd oracle
uv run --with pytest --python 3.12 pytest
set "RC=!errorlevel!"
popd
if not "!RC!"=="0" exit /b !RC!
echo.
echo === Rust engine ===
docker build -t privguard-engine engine || exit /b 1
docker run --rm privguard-engine || exit /b 1
echo.
echo All checks passed.
exit /b 0

:dev
echo Building api image...
call :build_with_retry "api" || exit /b 1
echo Starting db + api...
docker compose up -d db api || exit /b 1
call :api_port
if "!API_PORT!"=="" (
  echo The api service did not publish a port. Check: run.bat logs api
  exit /b 1
)
call :wait_for_api !API_PORT!
call :sync_env !API_PORT!
if not exist "frontend\node_modules" (
  echo Installing frontend dependencies...
  pushd frontend && call npm install && popd
)
echo.
rem Vite wants 5173 but steps to the next free port if something already holds
rem it, so let it announce its own URL rather than promising one here.
echo Starting the dev server. Vite prints its URL below.
echo API proxied to http://localhost:!API_PORT!. Ctrl+C stops the dev server; containers keep running.
echo.
cd frontend
call npm run dev
exit /b 0

:stop
docker compose stop
echo Stopped. Data is kept; run.bat to start again.
exit /b 0

:restart
docker compose stop
goto :up

:logs
if "%~2"=="" (
  docker compose logs -f --tail 100
) else (
  docker compose logs -f --tail 100 %2
)
exit /b 0

:status
docker compose ps
exit /b 0

:reset
echo.
echo This deletes the "pgdata" volume. Every row in Postgres is destroyed and
echo the database is rebuilt from migrations plus seed_demo.
echo.
set "CONFIRM="
set /p "CONFIRM=Type 'yes' to continue: "
if /i not "!CONFIRM!"=="yes" (
  echo Cancelled.
  exit /b 0
)
docker compose down -v || exit /b 1
goto :up

rem ----------------------------------------------------------------- helpers
rem npm install / pip install inside the image build reach out to the network,
rem and a single dropped connection on a slow link fails the whole build. Retry
rem a few times before giving up. %~1 is an optional service name; empty builds
rem everything.
:build_with_retry
set "BUILD_SVC=%~1"
set /a BUILD_TRY=0
:build_retry_loop
set /a BUILD_TRY+=1
docker compose build %BUILD_SVC%
if not errorlevel 1 exit /b 0
if !BUILD_TRY! geq 3 (
  echo.
  echo The image build failed after !BUILD_TRY! attempts. If this is a network
  echo timeout, check your connection and try again; otherwise read the error above.
  exit /b 1
)
echo.
echo Build attempt !BUILD_TRY! failed ^(often a transient npm/pip network hiccup^). Retrying...
ping -n 6 127.0.0.1 >nul
goto :build_retry_loop

rem uv manages the oracle's Python environment. It is the one host tool the
rem non-Docker commands need, so say plainly where to get it rather than
rem failing with "'uv' is not recognized".
:require_uv
uv --version >nul 2>&1
if not errorlevel 1 exit /b 0
echo uv was not found. It manages the Python environment for the oracle.
echo Install it with:  winget install astral-sh.uv
echo   or see:         https://docs.astral.sh/uv/getting-started/installation/
exit /b 1

:ensure_env
rem docker-compose.yml reads its secrets from a git-ignored .env and refuses to start
rem without them. First run: copy .env.example, replacing every __GENERATE__ with a
rem fresh random value. An existing .env is never touched.
if exist ".env" exit /b 0
if not exist ".env.example" (
  echo .env is missing and so is .env.example.
  exit /b 1
)
powershell -NoProfile -Command "$rng=[Security.Cryptography.RandomNumberGenerator]::Create(); $out=foreach($l in Get-Content '.env.example'){ while($l.Contains('__GENERATE__')){ $b=New-Object byte[] 36; $rng.GetBytes($b); $v=[Convert]::ToBase64String($b).Replace('+','-').Replace('/','_').TrimEnd('='); $i=$l.IndexOf('__GENERATE__'); $l=$l.Substring(0,$i)+$v+$l.Substring($i+12) }; $l }; [IO.File]::WriteAllLines('.env',[string[]]$out,(New-Object Text.UTF8Encoding($false)))" || exit /b 1
echo Created .env with freshly generated secrets (git-ignored; never commit it).
exit /b 0

:require_docker
docker compose version >nul 2>&1
if errorlevel 1 (
  echo Docker Compose was not found. Install Docker Desktop:
  echo   https://docs.docker.com/get-docker/
  exit /b 1
)
docker info >nul 2>&1
if not errorlevel 1 exit /b 0

rem The CLI ships with Docker Desktop, but the daemon only runs while Desktop is
rem open, so start it rather than just failing.
set "DD=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if not exist "%DD%" (
  echo The Docker daemon is not running. Start Docker and try again.
  exit /b 1
)
echo Starting Docker Desktop...
start "" "%DD%"
<nul set /p "=Waiting for the Docker daemon"
for /l %%i in (1,1,60) do (
  docker info >nul 2>&1
  if not errorlevel 1 (
    echo.
    echo Docker is ready.
    exit /b 0
  )
  <nul set /p "=."
  rem ping rather than timeout: timeout aborts when stdin is redirected, which
  rem it is whenever this script is driven by CI or another process.
  ping -n 6 127.0.0.1 >nul
)
echo.
echo The Docker daemon did not come up within 5 minutes.
exit /b 1

rem Never hardcode host ports: docker-compose.override.yml exists so a developer
rem can remap them around whatever else is bound, so ask Compose where the
rem service actually landed. Output looks like "0.0.0.0:8001".
:api_port
set "API_PORT="
for /f "tokens=2 delims=:" %%p in ('docker compose port api 8000 2^>nul') do set "API_PORT=%%p"
exit /b 0

:ui_port
set "UI_PORT="
for /f "tokens=2 delims=:" %%p in ('docker compose port ui 80 2^>nul') do set "UI_PORT=%%p"
exit /b 0

:db_port
set "DB_PORT="
for /f "tokens=2 delims=:" %%p in ('docker compose port db 5432 2^>nul') do set "DB_PORT=%%p"
exit /b 0

rem Any HTTP response means Django is serving. /auth/me/ answers 401 without a
rem token, which is a perfectly good readiness signal.
:wait_for_api
<nul set /p "=Waiting for the API on :%~1"
for /l %%i in (1,1,60) do (
  set "CODE="
  for /f %%c in ('curl -s -o NUL -m 3 -w "%%{http_code}" "http://localhost:%~1/api/auth/me/" 2^>nul') do set "CODE=%%c"
  if not "!CODE!"=="000" if not "!CODE!"=="" (
    echo.
    exit /b 0
  )
  <nul set /p "=."
  ping -n 3 127.0.0.1 >nul
)
echo.
echo The API did not answer in time. Check: run.bat logs api
exit /b 0

rem The browser talks to a relative /api in dev exactly as it does behind nginx
rem in production, so Vite proxies it. That proxy needs the API's real host
rem port. Rewrite only our own key and leave any other line in the file alone.
:sync_env
set "TARGET=http://localhost:%~1"
set "ENVFILE=frontend\.env"
set "WANTED=API_PROXY_TARGET=!TARGET!"

if exist "%ENVFILE%" (
  findstr /x /c:"!WANTED!" "%ENVFILE%" >nul 2>&1
  if not errorlevel 1 exit /b 0
)

rem Any other key the developer put here is kept, and kept first: an existing
rem file may open with a byte-order mark, which dotenv ignores at position 0
rem but would read as part of a key name anywhere else.
if exist "%ENVFILE%" (
  findstr /v /b /c:"API_PROXY_TARGET=" "%ENVFILE%" > "%ENVFILE%.tmp2" 2>nul
  findstr /v /b /c:"# Written by run." "%ENVFILE%.tmp2" > "%ENVFILE%.tmp" 2>nul
  del "%ENVFILE%.tmp2" >nul 2>&1
) else (
  type nul > "%ENVFILE%.tmp"
)
>> "%ENVFILE%.tmp" echo # Written by run.bat: Vite proxies /api to the port Compose published.
>> "%ENVFILE%.tmp" echo !WANTED!
move /y "%ENVFILE%.tmp" "%ENVFILE%" >nul
echo Wrote %ENVFILE% (API_PROXY_TARGET=!TARGET!)
exit /b 0

:print_urls
call :ui_port
call :api_port
call :db_port
echo.
echo PrivGuard is up.
echo.
if not "!UI_PORT!"=="" echo   App          http://localhost:!UI_PORT!
if not "!API_PORT!"=="" echo   API          http://localhost:!API_PORT!/api/
if not "!DB_PORT!"=="" echo   Postgres     localhost:!DB_PORT!  (privguard / privguard / safehealth)
echo.
echo   Sign in with any seeded principal, password privguard123:
echo     alice  analyst    full budget, populated burn-down
echo     frank  analyst    nearly exhausted budget
echo     dave   dba        every screen, policy administration
echo     erin   auditor    audit log and ledger
echo.
echo   run.bat dev     hot-reloading dev server
echo   run.bat logs    follow the logs
echo   run.bat stop    stop everything
echo.
exit /b 0

:help
echo.
rem findstr /n prefixes "<line>:", so token 1 is the number and token * is the
rem text. Trim the leading "rem " off the text before printing it.
for /f "tokens=1,* delims=:" %%a in ('findstr /n "^rem " "%~f0"') do (
  if %%a leq 19 (
    set "LINE=%%b"
    echo(!LINE:~4!
  )
)
echo.
exit /b 0
