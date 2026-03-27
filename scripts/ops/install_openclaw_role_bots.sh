#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-/opt/global-sentinel}"
SYSTEMD_DIR="${REPO_ROOT}/scripts/systemd"

echo "Installing OpenClaw role-bot services from ${SYSTEMD_DIR}"

sudo cp "${SYSTEMD_DIR}/global-sentinel-openclaw-ops.service" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-openclaw-research.service" /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now global-sentinel-openclaw-ops.service
sudo systemctl enable --now global-sentinel-openclaw-research.service

echo "Active OpenClaw role-bot services:"
sudo systemctl --no-pager --full status global-sentinel-openclaw-ops.service global-sentinel-openclaw-research.service | sed -n '1,160p'
