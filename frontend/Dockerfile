FROM node:25-alpine AS build
WORKDIR /app

# npm's default 5-minute socket timeout and single attempt make CI/Docker builds
# fragile on a slow or congested connection. Give it real retries and headroom.
ENV npm_config_fetch_retries=5 \
    npm_config_fetch_retry_factor=2 \
    npm_config_fetch_retry_mintimeout=20000 \
    npm_config_fetch_retry_maxtimeout=120000 \
    npm_config_fetch_timeout=600000

COPY package*.json ./
# package-lock.json is committed, so install straight from it: reproducible and
# faster than resolving the tree. Fall back to `npm install` if the lockfile and
# manifest ever drift.
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY . .
# Hidden by default: a public build must not print the repository's demo password on the sign-in page.
ARG VITE_SHOW_DEMO_ACCOUNTS=false
ENV VITE_SHOW_DEMO_ACCOUNTS=$VITE_SHOW_DEMO_ACCOUNTS
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# nginx renders every file in /etc/nginx/templates (*.template) with envsubst at start-up.
COPY 00-proto.conf.template /etc/nginx/templates/00-proto.conf.template
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV PORT=80 API_UPSTREAM=http://api:8000
EXPOSE 80
