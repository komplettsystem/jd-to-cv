#!/usr/bin/env bash
# Run once from the owner's machine: lets one SSH key (made for GitHub Actions) run deploy-receive.sh on the server, and nothing else.
# Usage: deploy/install-ci-key.sh <server-ip> <path to the public key, e.g. ~/.ssh/reverse-apply-deploy.pub>
set -euo pipefail
HOST="${1:?server ip}"
PUB="${2:?path to the .pub file}"
HERE="$(cd "$(dirname "$0")" && pwd)"
KEY="$(cat "$PUB")"
case "$KEY" in ssh-ed25519\ *) ;; *) echo "expected an ssh-ed25519 public key in $PUB" >&2; exit 1 ;; esac
scp -q "$HERE/deploy-receive.sh" "root@$HOST:/opt/reverse-apply/deploy-receive.sh"
# "restrict" turns off shell, port forwarding, agent forwarding and the terminal; "command" runs the receiver whatever the client asks for.
LINE="command=\"/opt/reverse-apply/deploy-receive.sh\",restrict $KEY"
ssh "root@$HOST" "chmod 700 /opt/reverse-apply/deploy-receive.sh && grep -qxF '$LINE' /root/.ssh/authorized_keys || echo '$LINE' >> /root/.ssh/authorized_keys"
echo "Installed. The key in $PUB can now only run /opt/reverse-apply/deploy-receive.sh on $HOST."
