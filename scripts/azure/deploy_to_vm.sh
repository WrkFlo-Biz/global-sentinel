#!/usr/bin/env bash
set -euo pipefail

# Global Sentinel - Deploy to Azure VM
# Usage: ./scripts/azure/deploy_to_vm.sh
#
# Deploys the global-sentinel repo to the Azure VM and sets up
# the crisis monitor as a systemd service.

VM_RG="openclaw-rg"
VM_NAME="openclaw-gateway-vm"
VM_IP="20.124.180.8"
REMOTE_DIR="/opt/global-sentinel"
SSH_USER="openclaw"

echo "=== Global Sentinel Deploy to Azure VM ==="
echo "Target: ${SSH_USER}@${VM_IP}:${REMOTE_DIR}"
echo ""

# 1. Sync repo to VM (exclude .git, logs, reports, __pycache__)
echo "[1/4] Syncing repo to VM..."
rsync -avz --delete \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'logs/' \
    --exclude 'reports/' \
    --exclude '.pytest_cache/' \
    --exclude 'tests/replays/*/out/' \
    -e "ssh -o StrictHostKeyChecking=no" \
    . "${SSH_USER}@${VM_IP}:${REMOTE_DIR}/"

# 2. Install Python dependencies on VM
echo "[2/4] Installing dependencies on VM..."
ssh "${SSH_USER}@${VM_IP}" "cd ${REMOTE_DIR} && pip3 install -r requirements.txt 2>/dev/null || pip install -r requirements.txt 2>/dev/null || echo 'pip install skipped (may need sudo)'"

# 3. Copy systemd service file
echo "[3/4] Setting up systemd service..."
ssh "${SSH_USER}@${VM_IP}" "sudo cp ${REMOTE_DIR}/scripts/systemd/global-sentinel.service /etc/systemd/system/global-sentinel.service 2>/dev/null && sudo systemctl daemon-reload && sudo systemctl enable global-sentinel || echo 'systemd setup skipped (check permissions)'"

# 4. Restart service
echo "[4/4] Restarting Global Sentinel service..."
ssh "${SSH_USER}@${VM_IP}" "sudo systemctl restart global-sentinel 2>/dev/null && sudo systemctl status global-sentinel --no-pager || echo 'Service restart skipped'"

echo ""
echo "=== Deploy complete ==="
echo "Check status: ssh ${SSH_USER}@${VM_IP} 'sudo systemctl status global-sentinel'"
echo "Check logs:   ssh ${SSH_USER}@${VM_IP} 'sudo journalctl -u global-sentinel -f'"
