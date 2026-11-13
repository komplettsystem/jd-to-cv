#!/usr/bin/env bash
# Read-only look at the production app: errors, recent reports, spend, and the app log. Nothing is changed, and no posting, CV, chat
# or letter text is shown (see inspect.py). Report links in the log are masked.
# Usage: deploy/inspect.sh <server-ip> errors|recent|spend|citations|verdicts|catalog|logs [lines]
set -euo pipefail
HOST="${1:?server ip}"
WHAT="${2:?errors | recent | spend | citations | verdicts | catalog | logs}"
case "$WHAT" in
  errors|recent|spend|citations|verdicts|catalog) ssh "root@$HOST" "docker exec -i reverse-apply-app-1 python - $WHAT" < "$(dirname "$0")/inspect.py" ;;   # the script is sent on stdin: the image does not contain deploy/
  logs) LINES="${3:-100}"; [[ "$LINES" =~ ^[0-9]+$ ]] || { echo "lines must be a number"; exit 1; }
        ssh "root@$HOST" "docker logs --tail $LINES reverse-apply-app-1 2>&1" | sed -E 's#/r/[A-Za-z0-9_-]+#/r/<token>#g' ;;
  *) echo "usage: inspect.sh <server-ip> errors|recent|spend|citations|verdicts|catalog|logs [lines]"; exit 1 ;;
esac
