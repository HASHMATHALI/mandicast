# syntax=docker/dockerfile:1
# MandiCast dashboard: static production build served by nginx (unprivileged), which also proxies /v1 to the API so
# the dashboard and the API share one origin (no CORS) and nginx sets the security headers (docs/15 §3).

# node:22-alpine (22.23.5), pinned by digest
FROM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS build
WORKDIR /src
COPY dashboard/package.json dashboard/package-lock.json ./
# Exact versions from the lockfile; no install scripts run.
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY dashboard/ ./
ARG VITE_API_BASE=same-origin
ARG VITE_BOT_USERNAME=
ENV VITE_API_BASE=${VITE_API_BASE} VITE_BOT_USERNAME=${VITE_BOT_USERNAME}
RUN npm run build

# nginx 1.29.8 (alpine, runs as uid 101 on port 8080), pinned by digest
FROM nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6
COPY docker/dashboard/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/dashboard/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
