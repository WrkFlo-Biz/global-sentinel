#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-/opt/global-sentinel}"
SYSTEMD_DIR="${REPO_ROOT}/scripts/systemd"

echo "Installing Global Sentinel operational automation units from ${SYSTEMD_DIR}"

sudo cp "${SYSTEMD_DIR}/global-sentinel-operational-audit.service" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-operational-audit.timer" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-blob-health.service" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-blob-health.timer" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-canary-observability.service" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-canary-observability.timer" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-canary-stabilization.service" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-canary-stabilization.timer" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-canary-checkpoint.service" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-canary-checkpoint.timer" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-evidence-canary.service" /etc/systemd/system/
sudo cp "${SYSTEMD_DIR}/global-sentinel-evidence-canary.timer" /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now global-sentinel-operational-audit.timer
sudo systemctl enable --now global-sentinel-blob-health.timer
sudo systemctl enable --now global-sentinel-canary-observability.timer
sudo systemctl enable --now global-sentinel-canary-stabilization.timer
sudo systemctl enable --now global-sentinel-canary-checkpoint.timer
sudo systemctl enable --now global-sentinel-evidence-canary.timer

echo "Operational automation timers enabled:"
sudo systemctl list-timers --all | grep -E 'global-sentinel-(operational-audit|blob-health|canary-observability|canary-stabilization|canary-checkpoint|evidence-canary)' || true
