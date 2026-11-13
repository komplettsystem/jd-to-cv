#!/usr/bin/env bash
# Runs ON THE SERVER: rebuild with fresh base images and restart. Also run weekly by cron (see cloud-init.yaml).
set -euo pipefail
cd /opt/reverse-apply/app/deploy
export COMPOSE_PROJECT_NAME=reverse-apply
docker compose --env-file /opt/reverse-apply/state/.env build --pull
docker compose --env-file /opt/reverse-apply/state/.env pull caddy
docker compose --env-file /opt/reverse-apply/state/.env up -d
docker image prune -f >/dev/null
