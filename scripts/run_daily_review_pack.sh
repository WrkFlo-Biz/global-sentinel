#!/usr/bin/env bash
set -euo pipefail

# Global Sentinel - Daily Review Pack Runner
#
# Purpose:
#   Run the daily analytics / review / recommendation pipeline in one command.
#   Safe for shadow/paper workflows. Does not apply config changes.
#
# Usage:
#   ./scripts/run_daily_review_pack.sh
#   REPO_ROOT=/path/to/repo PKG_DIR=reports/packages ./scripts/run_daily_review_pack.sh
#
# Optional env:
#   DATE_TAG (default UTC YYYYMMDD)
#   PKG_DIR
#   PAPER_ORDERS_DIR
#   PAPER_TRADES_DIR
#   PROPOSED_CFG (default proposals/thresholds_proposed.yaml)
#   CURRENT_CFG  (default config/thresholds.yaml)

REPO_ROOT="${REPO_ROOT:-$(pwd)}"
DATE_TAG="${DATE_TAG:-$(date -u +%Y%m%d)}"

PKG_DIR="${PKG_DIR:-$REPO_ROOT/reports/packages}"
AN_DIR="${AN_DIR:-$REPO_ROOT/reports/analytics/$DATE_TAG}"
REVIEW_DIR="${REVIEW_DIR:-$REPO_ROOT/reports/daily/$DATE_TAG}"

PAPER_ORDERS_DIR="${PAPER_ORDERS_DIR:-$REPO_ROOT/logs/paper_orders}"
PAPER_TRADES_DIR="${PAPER_TRADES_DIR:-$REPO_ROOT/logs/paper_trades}"

CURRENT_CFG="${CURRENT_CFG:-$REPO_ROOT/config/thresholds.yaml}"
PROPOSED_CFG="${PROPOSED_CFG:-$REPO_ROOT/proposals/thresholds_proposed.yaml}"

mkdir -p "$AN_DIR" "$REVIEW_DIR"

echo "=== Global Sentinel Daily Review Pack ==="
echo "Repo root: $REPO_ROOT"
echo "Packages : $PKG_DIR"
echo "Date tag : $DATE_TAG"

if [ ! -d "$PKG_DIR" ]; then
  echo "ERROR: package dir not found: $PKG_DIR"
  exit 1
fi

echo
echo "[1/13] TCA shadow report"
python "$REPO_ROOT/src/execution/tca_shadow_report.py" \
  --inputs "$PKG_DIR" \
  --output-json "$AN_DIR/tca_shadow_report.json" \
  --output-md "$AN_DIR/tca_shadow_report.md"

echo
echo "[2/13] No-trade quality"
python "$REPO_ROOT/src/metrics/no_trade_quality.py" \
  --inputs "$PKG_DIR" \
  --output-json "$AN_DIR/no_trade_quality.json"

echo
echo "[3/13] Paper trade reconciliation (optional)"
if [ -d "$PAPER_ORDERS_DIR" ] || [ -d "$PAPER_TRADES_DIR" ]; then
  set +e
  python "$REPO_ROOT/src/execution/paper_trade_reconciler.py" \
    --package-inputs "$PKG_DIR" \
    --broker-orders "$PAPER_ORDERS_DIR" \
    --broker-trades "$PAPER_TRADES_DIR" \
    --output-json "$AN_DIR/paper_trade_reconciliation.json"
  RC=$?
  set -e
  if [ $RC -ne 0 ]; then
    echo "WARN: reconciliation step failed (continuing)."
  fi
else
  echo "No paper order/trade dirs found; skipping reconciliation."
fi

echo
echo "[4/13] Daily decision review"
RECON_ARGS=()
if [ -f "$AN_DIR/paper_trade_reconciliation.json" ]; then
  RECON_ARGS+=(--recon-json "$AN_DIR/paper_trade_reconciliation.json")
fi

python "$REPO_ROOT/src/reports/daily_decision_review.py" \
  --package-inputs "$PKG_DIR" \
  --tca-json "$AN_DIR/tca_shadow_report.json" \
  --no-trade-json "$AN_DIR/no_trade_quality.json" \
  "${RECON_ARGS[@]}" \
  --output-json "$REVIEW_DIR/daily_decision_review.json" \
  --output-md "$REVIEW_DIR/daily_decision_review.md"

echo
echo "[5/13] Generate recommendation queue entries (post-close suggestions only)"
REC_ARGS=(--tca-json "$AN_DIR/tca_shadow_report.json" --no-trade-json "$AN_DIR/no_trade_quality.json")
if [ -f "$AN_DIR/paper_trade_reconciliation.json" ]; then
  REC_ARGS+=(--recon-json "$AN_DIR/paper_trade_reconciliation.json")
fi

python "$REPO_ROOT/src/self_improvement/recommendation_queue.py" \
  --repo-root "$REPO_ROOT" \
  generate-from-analytics \
  "${REC_ARGS[@]}" > "$AN_DIR/generated_recommendations.json"

echo
echo "[6/13] Threshold drift guard (optional if proposed config exists)"
if [ -f "$PROPOSED_CFG" ] && [ -f "$CURRENT_CFG" ]; then
  META_FILE="$AN_DIR/threshold_drift_metadata.json"
  cat > "$META_FILE" <<EOF
{
  "intraday_session": false,
  "market_hours_open": false,
  "replay_evidence_refs": [
    "$AN_DIR/tca_shadow_report.json",
    "$AN_DIR/no_trade_quality.json",
    "$REVIEW_DIR/daily_decision_review.json"
  ],
  "approvals": {
    "caio_reviewed": false,
    "cfo_reviewed": false
  }
}
EOF

  python "$REPO_ROOT/src/monitoring/threshold_drift_guard.py" \
    --current "$CURRENT_CFG" \
    --proposed "$PROPOSED_CFG" \
    --metadata-json "$META_FILE" \
    --output-json "$AN_DIR/threshold_drift_assessment.json"

  echo "Threshold drift assessment written."
else
  echo "No proposed/current threshold files found; drift guard skipped."
fi

echo
echo "[7/13] Execution reliability metrics snapshot"
python "$REPO_ROOT/src/execution/execution_reliability_metrics.py" \
  --repo-root "$REPO_ROOT" \
  --output-json "$AN_DIR/execution_reliability_metrics.json" \
  --output-md "$AN_DIR/execution_reliability_metrics.md"

echo
echo "[8/13] Manual review queue report snapshot"
python "$REPO_ROOT/src/reports/manual_review_queue_report.py" \
  --repo-root "$REPO_ROOT" \
  --output-json "$AN_DIR/manual_review_queue_report.json" \
  --output-md "$AN_DIR/manual_review_queue_report.md"

echo
echo "[9/13] Stale intent sweeper snapshot (time-window-aware TTL policy)"
python "$REPO_ROOT/src/execution/stale_intent_sweeper.py" \
  --repo-root "$REPO_ROOT" \
  --use-time-window-ttl-policy \
  --ttl-policy-yaml "$REPO_ROOT/config/order_ttl_policy.yaml" \
  --output-json "$AN_DIR/stale_intent_sweeper_report.json" \
  --output-md "$AN_DIR/stale_intent_sweeper_report.md"

echo
echo "[10/13] Reconciler lag SLA monitor snapshot"
python "$REPO_ROOT/src/monitoring/reconciler_lag_sla_monitor.py" \
  --repo-root "$REPO_ROOT" \
  --output-json "$AN_DIR/reconciler_lag_sla_monitor.json" \
  --output-md "$AN_DIR/reconciler_lag_sla_monitor.md"

echo
echo "[11/13] Manual review owner routing snapshot"
if [ -f "$AN_DIR/manual_review_queue_report.json" ]; then
  LAG_ARGS=()
  if [ -f "$AN_DIR/reconciler_lag_sla_monitor.json" ]; then
    LAG_ARGS+=(--lag-sla-report-json "$AN_DIR/reconciler_lag_sla_monitor.json")
  fi
  python "$REPO_ROOT/src/reports/manual_review_owner_router.py" \
    --manual-review-report-json "$AN_DIR/manual_review_queue_report.json" \
    "${LAG_ARGS[@]}" \
    --output-json "$AN_DIR/manual_review_owner_routing.json" \
    --output-md "$AN_DIR/manual_review_owner_routing.md"
else
  echo "No manual review queue report found; skipping owner routing."
fi

echo
echo "[12/13] Incident mode controller (advisory)"
INCIDENT_ARGS=()
if [ -f "$AN_DIR/reconciler_lag_sla_monitor.json" ]; then
  INCIDENT_ARGS+=(--lag-sla-json "$AN_DIR/reconciler_lag_sla_monitor.json")
fi
if [ -f "$AN_DIR/stale_intent_sweeper_report.json" ]; then
  INCIDENT_ARGS+=(--stale-sweeper-json "$AN_DIR/stale_intent_sweeper_report.json")
fi
if [ -f "$AN_DIR/execution_reliability_metrics.json" ]; then
  INCIDENT_ARGS+=(--exec-reliability-json "$AN_DIR/execution_reliability_metrics.json")
fi
python "$REPO_ROOT/src/monitoring/incident_mode_controller.py" \
  "${INCIDENT_ARGS[@]}" \
  --output-json "$AN_DIR/incident_assessment.json" \
  --output-md "$AN_DIR/incident_assessment.md"

echo
echo "[13/13] Owner writeback queue"
if [ -f "$AN_DIR/manual_review_owner_routing.json" ]; then
  python "$REPO_ROOT/src/reports/manual_review_owner_writeback.py" \
    --owner-routing-json "$AN_DIR/manual_review_owner_routing.json" \
    --output-jsonl "$REPO_ROOT/logs/ops/owner_writeback_queue.jsonl" \
    --output-summary-json "$AN_DIR/owner_writeback_summary.json"
else
  echo "No owner routing report found; skipping writeback queue."
fi

echo
echo "=== COMPLETE ==="
echo "Artifacts:"
echo "  Analytics: $AN_DIR"
echo "  Review   : $REVIEW_DIR"
echo "  Queue    : $REPO_ROOT/logs/self_improvement/recommendation_queue.jsonl"
