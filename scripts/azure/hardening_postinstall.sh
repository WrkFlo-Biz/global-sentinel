#!/usr/bin/env bash
set -euo pipefail

# Global Sentinel V4 — Post-install hardening (run ON Azure VM)
#
# Usage:
#   sudo bash scripts/azure/hardening_postinstall.sh

APP_USER="${APP_USER:-gsadmin}"
APP_HOME="${APP_HOME:-/home/${APP_USER}/global-sentinel}"
SSH_PORT="${SSH_PORT:-22}"
ALLOW_SSH_CIDR="${ALLOW_SSH_CIDR:-any}"
STRICT_SSH="${STRICT_SSH:-true}"
ENABLE_UFW="${ENABLE_UFW:-true}"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "==> [1/9] System updates"
apt-get update -y && apt-get upgrade -y

echo "==> [2/9] Installing dependencies"
apt-get install -y \
  ca-certificates curl wget gnupg lsb-release jq unzip zip git \
  build-essential python3 python3-pip python3-venv \
  rsync htop tmux vim ufw fail2ban chrony logrotate

echo "==> [3/9] Python toolchain"
python3 -m pip install --upgrade pip setuptools wheel 2>/dev/null || true

echo "==> [4/9] Creating app runtime directories"
mkdir -p "$APP_HOME"/{logs,reports,control,config/staging}
chown -R "$APP_USER:$APP_USER" "$APP_HOME" || true

echo "==> [5/9] Configuring fail2ban"
mkdir -p /etc/fail2ban/jail.d
cat > /etc/fail2ban/jail.d/sshd-local.conf <<EOF
[sshd]
enabled = true
port = ${SSH_PORT}
maxretry = 5
findtime = 10m
bantime = 1h
EOF
systemctl enable --now fail2ban || true

echo "==> [6/9] Configuring UFW"
if [[ "$ENABLE_UFW" == "true" ]]; then
  ufw default deny incoming || true
  ufw default allow outgoing || true
  if [[ "$ALLOW_SSH_CIDR" == "any" ]]; then
    ufw allow "${SSH_PORT}"/tcp || true
  else
    ufw allow from "$ALLOW_SSH_CIDR" to any port "$SSH_PORT" proto tcp || true
  fi
  yes | ufw enable || true
  ufw status verbose || true
fi

if [[ "$STRICT_SSH" == "true" ]]; then
  echo "==> [7/9] Hardening sshd_config"
  SSHD_CFG="/etc/ssh/sshd_config"
  cp "$SSHD_CFG" "${SSHD_CFG}.bak.$(date +%s)"
  sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/g' "$SSHD_CFG" || true
  sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin no/g' "$SSHD_CFG" || true
  sed -i 's/^#\?PubkeyAuthentication .*/PubkeyAuthentication yes/g' "$SSHD_CFG" || true
  grep -q "^ClientAliveInterval" "$SSHD_CFG" && \
    sed -i 's/^ClientAliveInterval .*/ClientAliveInterval 300/g' "$SSHD_CFG" || \
    echo "ClientAliveInterval 300" >> "$SSHD_CFG"
  grep -q "^ClientAliveCountMax" "$SSHD_CFG" && \
    sed -i 's/^ClientAliveCountMax .*/ClientAliveCountMax 2/g' "$SSHD_CFG" || \
    echo "ClientAliveCountMax 2" >> "$SSHD_CFG"
  systemctl restart ssh || systemctl restart sshd || true
else
  echo "==> [7/9] SSH hardening skipped (STRICT_SSH=false)"
fi

echo "==> [8/9] Configuring logrotate"
cat > /etc/logrotate.d/global-sentinel <<'EOF'
/home/gsadmin/global-sentinel/logs/*.log
/home/gsadmin/global-sentinel/logs/**/*.json {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    maxsize 50M
}
EOF

echo "==> [9/9] Sysctl tuning + time sync"
cat > /etc/sysctl.d/99-global-sentinel.conf <<'EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
EOF
sysctl --system >/dev/null || true
systemctl enable --now chrony || true

echo ""
echo "=== Hardening Complete ==="
echo "IMPORTANT: Verify SSH access from another session before closing this one."
echo "Next: Clone repo, create .env, install systemd services."
