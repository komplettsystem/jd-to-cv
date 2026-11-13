#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 or Debian 13 server, from your own machine (works on any provider):
#   ssh root@<server-ip> 'bash -s' < deploy/setup-server.sh
# Installs Docker, a swap file, a firewall (22, 80, 443), fail2ban, automatic security updates with a night-time reboot,
# and a weekly rebuild. Safe to run again. Put your SSH key on the server first (ssh-copy-id root@<server-ip>): password
# logins are switched off only when a key is already installed.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y && apt-get upgrade -y
# The Compose 2 package is called docker-compose-v2 on Ubuntu and docker-compose on Debian.
. /etc/os-release
case "${ID:-}" in
  ubuntu) COMPOSE_PKG=docker-compose-v2 ;;
  debian) COMPOSE_PKG=docker-compose ;;
  *) echo "Unsupported system: ${PRETTY_NAME:-unknown}. Use Ubuntu 24.04 or Debian 13."; exit 1 ;;
esac
apt-get install -y docker.io "$COMPOSE_PKG" unattended-upgrades fail2ban ufw
systemctl enable --now docker fail2ban
docker compose version >/dev/null 2>&1 || { echo "The 'docker compose' command is missing after installing $COMPOSE_PKG."; exit 1; }

if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

cat > /etc/apt/apt.conf.d/52reboot <<'EOF2'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
EOF2
cat > /etc/cron.d/reverse-apply-update <<'EOF2'
# Weekly rebuild with fresh base images; does nothing until the first deploy has put the code in place.
0 3 * * 0 root [ -x /opt/reverse-apply/app/deploy/update.sh ] && /opt/reverse-apply/app/deploy/update.sh >> /var/log/reverse-apply-update.log 2>&1
EOF2

ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

if [ -s /root/.ssh/authorized_keys ]; then
  echo 'PasswordAuthentication no' > /etc/ssh/sshd_config.d/50-no-password.conf
  systemctl reload ssh || systemctl reload sshd
else
  echo "WARNING: no SSH key installed for root, so password logins stay on. Run ssh-copy-id, then run this script again."
fi

mkdir -p /opt/reverse-apply/state/data
echo "Server ready. Next: deploy/deploy.sh <server-ip>"
