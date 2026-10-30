# Live demo from a laptop through a tunnel

A production-mode copy of the app (DEBUG off, strong passwords, no no-login monitor key) that runs in
Docker on one machine and is reached over a public HTTPS address through ngrok. Free, no cold starts,
but it is only up while the machine is on, awake and online.

```bash
# once: secrets (git-ignored)
umask 077
cat > .env.live <<EOF
LIVE_DB_PASSWORD=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -hex 32)
PRIVGUARD_SERVER_SECRET=$(openssl rand -hex 32)
PRIVGUARD_DEMO_PASSWORD=$(openssl rand -base64 18 | tr -d '=+/')
PRIVGUARD_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '=+/')
EOF

# start (builds the images, migrates, seeds the five accounts, serves on 127.0.0.1:8090)
docker compose -f docker-compose.live.yml --env-file .env.live up -d --build

# publish (needs `ngrok config add-authtoken ...` once); the URL is printed and at http://127.0.0.1:4040
nohup ngrok http 8090 --log=stdout > /tmp/ngrok.log 2>&1 &
curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "import sys,json; print(json.load(sys.stdin)['tunnels'][0]['public_url'])"
```

**Accounts.** `alice`, `frank`, `erin`, `carol` use `PRIVGUARD_DEMO_PASSWORD`; `dave` (administrator)
uses `PRIVGUARD_ADMIN_PASSWORD`. Print them in your own terminal when you need them
(`grep PASSWORD .env.live`); do not paste them into chat or commit them. `privguard123` does not work.

**What visitors see.** ngrok's free plan shows a one-time "You are about to visit..." page; they click
*Visit Site*. A Cloudflare quick tunnel (`cloudflared tunnel --url http://localhost:8090`) has no such
page but gives a random address each run (the compose file already allows `*.trycloudflare.com`).

**Admin pages.** `/admin/` signs in as `dave`; after that `/monitor/database/` shows the schema and rows.

**Keep it up.** The containers restart with Docker; `ngrok` does not survive a reboot or a closed
terminal session started without `nohup`. Stop sleep while people use it (`caffeinate -dis` in a terminal).

**Stop and clean up.** `pkill -f "ngrok http 8090"`, then
`docker compose -f docker-compose.live.yml --env-file .env.live down -v` (this deletes the live database).
