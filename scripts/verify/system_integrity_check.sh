#!/usr/bin/env bash
set -uo pipefail

# Global Sentinel — System Integrity Check
# Prints Green/Yellow/Red summary per subsystem
# Exits non-zero if any Red condition

REPO_ROOT="${1:-/opt/global-sentinel}"
RED=0
YELLOW=0
GREEN=0
RESULTS=""

check() {
    local name="$1" color="$2" note="$3"
    RESULTS="${RESULTS}${name}|${color}|${note}\n"
    case "$color" in
        GREEN)  GREEN=$((GREEN+1)) ;;
        YELLOW) YELLOW=$((YELLOW+1)) ;;
        RED)    RED=$((RED+1)) ;;
    esac
}

echo "=========================================="
echo " GLOBAL SENTINEL — SYSTEM INTEGRITY CHECK"
echo "=========================================="
echo "Repo: $REPO_ROOT"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# --- 1. Governance ---
if [ -f "$REPO_ROOT/CLAUDE.md" ]; then
    if grep -q "shadow" "$REPO_ROOT/CLAUDE.md" && grep -q "NO LIVE ORDERS" "$REPO_ROOT/CLAUDE.md"; then
        check "governance" "GREEN" "CLAUDE.md present with shadow-only + safety gates"
    else
        check "governance" "YELLOW" "CLAUDE.md present but safety gates unclear"
    fi
else
    check "governance" "RED" "CLAUDE.md missing"
fi

# --- 2. Control Files ---
if [ -f "$REPO_ROOT/control/manual_veto.json" ] && [ -f "$REPO_ROOT/control/kill_switch.json" ]; then
    check "safety_controls" "GREEN" "manual_veto.json + kill_switch.json present"
else
    check "safety_controls" "RED" "Missing safety control files"
fi

# --- 3. Config Files ---
MISSING_CONFIGS=""
for cfg in thresholds.yaml assets_watchlist.yaml macro_policy_intel.yaml idiosyncratic_package.yaml; do
    [ ! -f "$REPO_ROOT/config/$cfg" ] && MISSING_CONFIGS="$MISSING_CONFIGS $cfg"
done
if [ -z "$MISSING_CONFIGS" ]; then
    check "config_files" "GREEN" "All core config files present"
else
    check "config_files" "RED" "Missing:$MISSING_CONFIGS"
fi

# --- 4. Data Bridges ---
BRIDGE_COUNT=0
MISSING_BRIDGES=""
for bridge in aviation_disruption market_microstructure gdelt finnhub fred eia \
    gcp_consciousness politician_alpha fed_board treasury_ofac whitehouse_policy \
    bls_releases narrative_velocity options_greeks exa_search; do
    if ls "$REPO_ROOT"/src/bridges/*${bridge}* >/dev/null 2>&1; then
        BRIDGE_COUNT=$((BRIDGE_COUNT+1))
    else
        MISSING_BRIDGES="$MISSING_BRIDGES $bridge"
    fi
done
if [ $BRIDGE_COUNT -ge 14 ]; then
    check "data_bridges" "GREEN" "$BRIDGE_COUNT/15 bridges found"
elif [ $BRIDGE_COUNT -ge 10 ]; then
    check "data_bridges" "YELLOW" "$BRIDGE_COUNT/15 ($MISSING_BRIDGES missing)"
else
    check "data_bridges" "RED" "Only $BRIDGE_COUNT/15 bridges"
fi

# --- 5. Scoring Engine ---
if [ -f "$REPO_ROOT/src/scoring/regime_shift.py" ]; then
    COMPONENTS=$(grep -c "_score_" "$REPO_ROOT/src/scoring/regime_shift.py" 2>/dev/null || echo 0)
    if [ "$COMPONENTS" -ge 10 ]; then
        check "regime_scorer" "GREEN" "$COMPONENTS scoring methods found"
    else
        check "regime_scorer" "YELLOW" "Only $COMPONENTS scoring methods"
    fi
else
    check "regime_scorer" "RED" "regime_shift.py missing"
fi

# --- 6. Execution Pipeline ---
EXEC_OK=true
for f in trade_idea_packager.py shadow_order_router.py alpaca_paper_adapter.py; do
    [ ! -f "$REPO_ROOT/src/execution/$f" ] && EXEC_OK=false
done
if $EXEC_OK; then
    check "execution_pipeline" "GREEN" "Packager + Router + Adapter present"
else
    check "execution_pipeline" "RED" "Missing execution components"
fi

# --- 7. Crisis Monitor ---
if [ -f "$REPO_ROOT/src/monitoring/crisis_monitor.py" ]; then
    check "crisis_monitor" "GREEN" "Main loop present"
else
    check "crisis_monitor" "RED" "crisis_monitor.py missing"
fi

# --- 8. Dashboard ---
if [ -f "$REPO_ROOT/dashboard/api/server.py" ] && [ -d "$REPO_ROOT/dashboard/frontend/src" ]; then
    check "dashboard" "GREEN" "API + Frontend present"
else
    check "dashboard" "YELLOW" "Dashboard partially configured"
fi

# --- 9. Risk Gates ---
RISK_FILES=""
[ -f "$REPO_ROOT/src/risk/market_impact_square_root.py" ] && RISK_FILES="impact "
[ -f "$REPO_ROOT/src/risk/impact_budget_gate.py" ] && RISK_FILES="${RISK_FILES}gate "
if [ -n "$RISK_FILES" ]; then
    check "econophysics" "GREEN" "Found: $RISK_FILES"
else
    check "econophysics" "YELLOW" "Econophysics files not found (basic risk gates only)"
fi

# --- 10. Time Window Policy ---
if [ -f "$REPO_ROOT/src/alpha/time_window_policy.py" ]; then
    check "time_window" "GREEN" "time_window_policy.py present"
elif grep -rl "time_window" "$REPO_ROOT/src/" >/dev/null 2>&1; then
    check "time_window" "GREEN" "Time window logic found in source"
else
    check "time_window" "YELLOW" "Time window policy not found"
fi

# --- 11. Python Compilation ---
if python3 -m compileall -q "$REPO_ROOT/src/" 2>/dev/null; then
    check "python_compile" "GREEN" "All .py files compile cleanly"
else
    check "python_compile" "RED" "Compilation errors found"
fi

# --- 12. Terminology Guard ---
LEGACY=$(grep -rn "submitted_orders_count" "$REPO_ROOT/src/" 2>/dev/null | grep -v "\.pyc" | wc -l | tr -d ' ')
if [ "$LEGACY" = "0" ]; then
    check "terminology" "GREEN" "No legacy terminology found"
else
    check "terminology" "YELLOW" "$LEGACY instances of legacy terminology"
fi

# --- 13. Systemd Services ---
if [ -d "$REPO_ROOT/scripts/systemd" ]; then
    SVC_COUNT=$(ls "$REPO_ROOT"/scripts/systemd/*.service 2>/dev/null | wc -l | tr -d ' ')
    check "systemd" "GREEN" "$SVC_COUNT service files"
else
    check "systemd" "YELLOW" "No systemd service files found"
fi

# --- 14. Env / Secrets ---
if [ -f "$REPO_ROOT/.env" ]; then
    check "env_secrets" "GREEN" ".env file present"
else
    check "env_secrets" "YELLOW" "No .env (secrets may be in systemd env)"
fi

# --- Print Summary ---
echo ""
echo "=========================================="
echo " SUBSYSTEM STATUS"
echo "=========================================="
printf "%-25s %-8s %s\n" "SUBSYSTEM" "STATUS" "NOTES"
printf "%-25s %-8s %s\n" "-------------------------" "--------" "-----"

echo -e "$RESULTS" | while IFS='|' read -r name color note; do
    [ -z "$name" ] && continue
    case "$color" in
        GREEN)  marker="[OK]" ;;
        YELLOW) marker="[!!]" ;;
        RED)    marker="[XX]" ;;
        *)      marker="[??]" ;;
    esac
    printf "%-25s %-8s %s\n" "$name" "$marker" "$note"
done

echo ""
echo "=========================================="
echo " SUMMARY: GREEN=$GREEN  YELLOW=$YELLOW  RED=$RED"
echo "=========================================="

if [ "$RED" -gt 0 ]; then
    echo "STATUS: NO-GO — $RED Red conditions require immediate attention"
    exit 1
elif [ "$YELLOW" -gt 0 ]; then
    echo "STATUS: CONDITIONAL GO — $YELLOW Yellow conditions should be reviewed"
    exit 0
else
    echo "STATUS: GO — All systems Green"
    exit 0
fi
