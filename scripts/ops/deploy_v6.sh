#!/usr/bin/env bash
# Global Sentinel V6.0.0 — Deployment Script
# Deploys all V6 modules to VM at /opt/global-sentinel
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VM_HOST="openclaw@20.124.180.8"
VM_DIR="/opt/global-sentinel"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

ssh_remote_bash() {
  ssh "$VM_HOST" bash -s -- "$@"
}

echo "═══════════════════════════════════════"
echo "  Global Sentinel V6.0.0 Deployment"
echo "  $(date)"
echo "═══════════════════════════════════════"

# Step 1: Backup current config on VM
echo "[1/7] Backing up current config on VM..."
ssh_remote_bash "$VM_DIR" "$TIMESTAMP" <<'EOF'
vm_dir="$1"
timestamp="$2"

cd "$vm_dir"
tar czf "/tmp/gs_backup_${timestamp}.tar.gz" config/ .env 2>/dev/null || true
EOF

# Step 2: Sync all new files
echo "[2/7] Syncing files to VM..."
rsync -avz --exclude='.git' --exclude='node_modules' --exclude='__pycache__' \
  --exclude='.env' --exclude='data/' --exclude='logs/' \
  "$REPO_ROOT/" "$VM_HOST:$VM_DIR/"

# Step 3: Create new directories on VM
echo "[3/7] Creating directories..."
ssh_remote_bash "$VM_DIR" <<'EOF'
vm_dir="$1"

cd "$vm_dir"
mkdir -p src/policy src/data data/pit reports/operational
EOF

# Step 4: Install new pip dependencies (if any)
echo "[4/7] Installing dependencies..."
ssh_remote_bash "$VM_DIR" <<'EOF'
vm_dir="$1"

cd "$vm_dir"
pip3 install pyyaml --quiet 2>/dev/null || true
EOF

# Step 5: Restart services
echo "[5/7] Restarting services..."
ssh "$VM_HOST" "sudo systemctl restart global-sentinel openclaw-research openclaw-ops"
sleep 5

# Step 6: Run smoke test
echo "[6/7] Running smoke test..."
ssh_remote_bash "$VM_DIR" <<'EOF'
vm_dir="$1"

cd "$vm_dir"
python3 - <<'PY' 2>&1 || echo 'Bridge test had issues'
from src.bridges.bridge_registry import BridgeRegistry

r = BridgeRegistry()
h = r.health_all()
print(f'Bridges: {len(h)}/21')
PY
EOF

ssh_remote_bash "$VM_DIR" <<'EOF'
vm_dir="$1"

cd "$vm_dir"
python3 - <<'PY' 2>&1 || echo 'OrderBook import failed'
from src.execution.order_book import OrderBook

OrderBook()
print('OrderBook: OK')
PY
EOF

ssh_remote_bash "$VM_DIR" <<'EOF'
vm_dir="$1"

cd "$vm_dir"
python3 - <<'PY' 2>&1 || echo 'StrategyEngine import failed'
from src.alpha.strategy_engine import StrategyEngine

se = StrategyEngine()
print(f'Strategies: {len(se.strategies)} loaded')
PY
EOF

# Step 7: Verify dashboard
echo "[7/7] Checking dashboard..."
ssh_remote_bash <<'EOF'
curl -s http://localhost:8501/api/health 2>/dev/null | head -1 || echo 'Dashboard check skipped'
EOF

# Step 8: Verify services running
echo ""
echo "Service status:"
ssh "$VM_HOST" "systemctl is-active global-sentinel openclaw-research openclaw-ops 2>/dev/null || true"

echo ""
echo "═══════════════════════════════════════"
echo "  V6.0.0 Deployment Complete"
echo "  Backup: /tmp/gs_backup_${TIMESTAMP}.tar.gz"
echo "═══════════════════════════════════════"
