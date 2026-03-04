#!/usr/bin/env bash
# Global Sentinel V4 — GitHub Enterprise Bootstrap
# Sets up repo, branch protections, CODEOWNERS, Actions, scanning.
set -euo pipefail

# === Configuration ===
GH_HOST="${GH_ENTERPRISE_HOST:-github.com}"
GH_ORG="${GH_ENTERPRISE_ORG:-YOUR_ORG}"
GH_REPO="${GH_ENTERPRISE_REPO:-global-sentinel}"
DEFAULT_BRANCH="main"

echo "=== Global Sentinel V4 — GitHub Enterprise Bootstrap ==="
echo "Host: $GH_HOST"
echo "Org: $GH_ORG"
echo "Repo: $GH_REPO"

# --- Authenticate ---
echo "[1/6] Checking authentication..."
gh auth status --hostname "$GH_HOST" || {
  echo "Please run: gh auth login --hostname $GH_HOST"
  exit 1
}

# --- Create repo (if needed) ---
echo "[2/6] Creating repository (if not exists)..."
gh repo view "$GH_ORG/$GH_REPO" --hostname "$GH_HOST" 2>/dev/null || \
  gh repo create "$GH_ORG/$GH_REPO" \
    --private \
    --description "Global Sentinel V4 — Geopolitical Risk Intelligence System" \
    --hostname "$GH_HOST"

# --- Branch protection ---
echo "[3/6] Setting branch protection on main..."
gh api "repos/$GH_ORG/$GH_REPO/branches/$DEFAULT_BRANCH/protection" \
  --hostname "$GH_HOST" \
  --method PUT \
  --input - <<'PROTECTION' || echo "Branch protection may require admin permissions"
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["lint", "test", "replay-check"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null
}
PROTECTION

# --- Enable vulnerability alerts ---
echo "[4/6] Enabling security features..."
gh api "repos/$GH_ORG/$GH_REPO/vulnerability-alerts" \
  --hostname "$GH_HOST" \
  --method PUT 2>/dev/null || echo "Vulnerability alerts may already be enabled"

# --- Create labels ---
echo "[5/6] Creating issue labels..."
for LABEL in "risk-config:Threshold or risk config change:e11d48" \
             "safety:Safety-related change:d73a4a" \
             "self-improvement:Auto-proposed improvement:0e8a16" \
             "ops:Infrastructure/ops change:1d76db" \
             "research:Signal/model change:5319e7"; do
  IFS=':' read -r NAME DESC COLOR <<< "$LABEL"
  gh label create "$NAME" --description "$DESC" --color "$COLOR" \
    --repo "$GH_ORG/$GH_REPO" --hostname "$GH_HOST" 2>/dev/null || true
done

# --- Self-hosted runner setup instructions ---
echo "[6/6] Self-hosted runner setup..."
echo ""
echo "To set up a self-hosted runner on your Azure VM:"
echo "  1. Go to: https://$GH_HOST/$GH_ORG/$GH_REPO/settings/actions/runners/new"
echo "  2. Follow the instructions to download and configure the runner"
echo "  3. Install as a service: sudo ./svc.sh install && sudo ./svc.sh start"
echo ""
echo "=== GitHub Enterprise Bootstrap Complete ==="
