#!/usr/bin/env bash
# Runs ON THE SERVER as the forced command of the GitHub deploy key (see install-ci-key.sh). The key can run nothing else.
# Input: a tar of the repository files on stdin, and the commit id as the SSH command (SSH_ORIGINAL_COMMAND).
# Does what deploy.sh does, except that it never touches state/.env: the OpenRouter key and the other secrets stay where deploy.sh put them.
set -euo pipefail
ROOT="${REVERSE_APPLY_ROOT:-/opt/reverse-apply}"   # overridable for the test in tests/test_deploy_receive.py
SHA="${SSH_ORIGINAL_COMMAND:-}"
[[ "$SHA" =~ ^[0-9a-f]{7,40}$ ]] || { echo "expected a commit id as the command" >&2; exit 1; }
[ -f "$ROOT/state/.env" ] || { echo "$ROOT/state/.env is missing: run deploy/deploy.sh from the owner's machine once first" >&2; exit 1; }
NEW="$ROOT/app.new"
rm -rf "$NEW" && mkdir -p "$NEW"
tar -x -C "$NEW" --no-same-owner
[ -f "$NEW/deploy/update.sh" ] || { echo "the upload has no deploy/update.sh, nothing was changed" >&2; rm -rf "$NEW"; exit 1; }
echo "${SHA:0:7}" > "$NEW/VERSION"
mkdir -p "$ROOT/state/data"
rm -rf "$ROOT/app" && mv "$NEW" "$ROOT/app"
chown -R 1001:1001 "$ROOT/state/data" 2>/dev/null || true
chmod 700 "$ROOT/state/data"
if [ -n "${REVERSE_APPLY_ROOT:-}" ]; then echo "test mode: not running update.sh"; exit 0; fi
exec "$ROOT/app/deploy/update.sh"
