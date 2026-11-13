#!/usr/bin/env bash
# Ship the COMMITTED code to the server and (re)start the app. The public CV is uploaded in the app's admin (Settings).
# Usage (from anywhere): deploy/deploy.sh <server-ip>
# Needs deploy/.env (copy .env.example). Code lives in /opt/reverse-apply/app and is replaced on every deploy; everything
# that must survive (database, CV, secret key, .env) lives in /opt/reverse-apply/state and is never touched by the code copy.
set -euo pipefail
HOST="${1:?server ip}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
[ -f "$HERE/.env" ] || { echo "Create $HERE/.env first (copy .env.example)"; exit 1; }
PREFIX="$(git -C "$ROOT" rev-parse --show-prefix)"
git -C "$ROOT" diff --quiet HEAD -- . || { echo "Uncommitted changes in $ROOT: commit first, only committed files are shipped."; exit 1; }
SSH="ssh root@$HOST"
$SSH "mkdir -p /opt/reverse-apply/state/data && rm -rf /opt/reverse-apply/app && mkdir -p /opt/reverse-apply/app"
TOP="$(git -C "$ROOT" rev-parse --show-toplevel)"   # git archive must run at the top of the repository
git -C "$TOP" archive "HEAD:${PREFIX%/}" | $SSH "tar -x -C /opt/reverse-apply/app"
git -C "$TOP" rev-parse --short HEAD | $SSH "cat > /opt/reverse-apply/app/VERSION"   # the commit this copy was made from, shown on eval runs
scp -q "$HERE/.env" "root@$HOST:/opt/reverse-apply/state/.env"
$SSH "chmod 600 /opt/reverse-apply/state/.env && chown -R 1001:1001 /opt/reverse-apply/state/data && chmod 700 /opt/reverse-apply/state/data"
$SSH "/opt/reverse-apply/app/deploy/update.sh"
echo "Deployed. Open https://$(grep '^DOMAIN=' "$HERE/.env" | cut -d= -f2)"
