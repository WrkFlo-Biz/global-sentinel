# Codex Patch Review Checklist

## Purpose
This checklist is used by Claude Code to review any patch produced by Codex before merging to main. Every gate must pass before the patch is deployed to the VM at `/opt/global-sentinel`.

---

## Pre-Merge Gates

### Functional Correctness
- [ ] All tests pass (`pytest tests/` — zero failures, zero errors)
- [ ] No test files deleted or skipped without explanation
- [ ] Dry-run broker audit works on VM (`python3 scripts/ops/run_broker_order_audit.py --dry-run`)
- [ ] Oil regime shows in scorecard (`v6_oil_regime` key present in cycle output)
- [ ] Oil regime shows in Telegram digest (regime line in digest message)
- [ ] Scanner remains informational-only (no order submission from scanner)
- [ ] No guardrails silently weakened (compare before/after values in config)
- [ ] Kill switch still works (test: set active=true, verify orders blocked)
- [ ] No new imports that don't exist on VM (`pip freeze` cross-check)

### Risk & Safety
- [ ] Guardrail values unchanged unless explicitly specified:
  - Max gross exposure: 200%
  - Max sector: 50%
  - Max single position: 8%
  - Daily loss halt: $3,000
  - Correlation group: 40%
- [ ] Kill switch integration preserved in all new code paths
- [ ] No order submission without guardrail check
- [ ] No position sizing changes without explicit spec reference
- [ ] Oil regime fallback to NORMAL when data unavailable
- [ ] Hysteresis prevents regime oscillation at boundaries
- [ ] DISLOCATION requires sustained price (not single-cycle spike)

### Code Quality
- [ ] No hardcoded API keys, tokens, or secrets
- [ ] All new modules wrapped in try/except in `crisis_monitor.py`
- [ ] New strategies in `war_strategies.yaml` follow existing format:
  ```yaml
  strategy_name:
    enabled: true
    weight: 1.0
    min_confidence: 0.6
    max_position_pct: 0.08
    description: "..."
  ```
- [ ] Test coverage for classification logic (broker audit buckets)
- [ ] Test coverage for oil regime thresholds and transitions
- [ ] Graceful degradation if oil price unavailable (both sources)
- [ ] Graceful degradation if chokepoint score unavailable
- [ ] Logging at appropriate levels (INFO for state changes, WARNING for fallbacks, ERROR for failures)
- [ ] No `print()` statements (use `logger` throughout)

### Integration
- [ ] New modules registered in `crisis_monitor.py` main loop
- [ ] New config files added to `config/` directory (not hardcoded)
- [ ] New data paths use existing `data/` directory structure
- [ ] Telegram digest format unchanged for existing fields
- [ ] Dashboard (Streamlit) not broken by new imports
- [ ] Rate limiter respected for new API calls
- [ ] PIT DataStore captures new regime/audit data in snapshots

### Broker Audit Specific
- [ ] Classification logic matches `broker_cleanup_spec.md` definitions
- [ ] All 7 buckets implemented (pending_close, stale_open, duplicate, crypto_orphan, partial_fill, rejected, position_leak)
- [ ] Dry-run mode takes NO broker actions (read-only)
- [ ] Execute mode only cancels stale/duplicate/orphan (never pending_close)
- [ ] Partial fills flagged for review (not auto-actioned)
- [ ] Position leak detection alerts via Telegram
- [ ] Audit log written to `data/audit/` with proper JSON format
- [ ] Both accounts audited (PA3F6696XKWK and PA36T8OFBNXB)
- [ ] Account-specific rules applied (day-trade: close by EOD; medium-long: 7-day threshold)

### Oil Regime Specific
- [ ] All 4 regimes implemented (NORMAL, ELEVATED, SHOCK, DISLOCATION)
- [ ] Price thresholds match spec ($95/$100/$105)
- [ ] Velocity trigger (5% 24h change) works independently of price
- [ ] Chokepoint triggers work independently of price
- [ ] Hysteresis implemented (2% below threshold, 3 cycles)
- [ ] DISLOCATION requires 2 consecutive cycles above $105
- [ ] Strategy promotion matrix matches spec weights
- [ ] Risk overlay caps match spec per regime
- [ ] Configuration in `config/oil_shock_regime.yaml` (not hardcoded)
- [ ] Fallback regime is NORMAL when data unavailable
- [ ] Regime state persists across kill switch activation

---

## Review Process

### Step 1: Automated Checks
```bash
cd /opt/global-sentinel
git fetch origin
git diff origin/main...codex/branch-name --stat  # see what changed
git diff origin/main...codex/branch-name           # full diff

# Run tests
pytest tests/ -v --tb=short 2>&1 | tail -50

# Check for new dependencies
git diff origin/main...codex/branch-name -- requirements.txt
```

### Step 2: Config Diff
```bash
# Check guardrail values haven't changed
git diff origin/main...codex/branch-name -- config/

# Specifically check:
git diff origin/main...codex/branch-name -- config/guardrails.yaml
git diff origin/main...codex/branch-name -- config/war_strategies.yaml
git diff origin/main...codex/branch-name -- config/execution_mode.yaml
```

### Step 3: Safety Audit
```bash
# Check for hardcoded secrets
grep -rn 'PKWR\|PKV3\|cRAaw\|api_key\s*=' src/ scripts/ --include='*.py' | grep -v '.env' | grep -v 'os.environ'

# Check kill switch references
grep -rn 'kill_switch' src/ --include='*.py'

# Check scanner doesn't submit orders
grep -rn 'submit_order\|place_order' src/scanner/ --include='*.py'

# Check all new modules have try/except in crisis_monitor
grep -A5 'import.*oil_shock\|import.*broker.*audit' src/crisis_monitor.py
```

### Step 4: Dry-Run Validation
```bash
# Test broker audit dry-run
python3 scripts/ops/run_broker_order_audit.py --dry-run

# Test oil regime evaluation
python3 -c "
from src.regime.oil_shock_regime import OilShockRegime
regime = OilShockRegime()
# Test each boundary
for price in [94.0, 95.0, 100.0, 105.0, 110.0]:
    result = regime.evaluate(wti_price=price)
    print(f'WTI \${price:.0f} -> {result.regime}')
"

# Test graceful degradation
python3 -c "
from src.regime.oil_shock_regime import OilShockRegime
regime = OilShockRegime()
result = regime.evaluate(wti_price=None)  # no data
print(f'No data -> {result.regime}')  # should be NORMAL
"
```

### Step 5: Integration Smoke Test
```bash
# Run one cycle manually (if possible)
python3 -c "
from src.crisis_monitor import CrisisMonitor
cm = CrisisMonitor()
scorecard = cm.run_cycle()
print(f'Oil regime in scorecard: {\"v6_oil_regime\" in scorecard}')
print(f'Cycle completed: {scorecard.get(\"cycle_complete\", False)}')
"
```

---

## Merge Decision

| Result | Action |
|--------|--------|
| All gates pass | Merge to main, deploy to VM |
| Tests fail | Fix tests first, re-review |
| Guardrails weakened | Reject — requires explicit spec change |
| Kill switch broken | Reject — critical safety feature |
| Missing graceful degradation | Fix before merge |
| New dependency not on VM | Add to requirements.txt, verify pip install works |
| Scanner submits orders | Reject — scanner is informational-only |

---

## Post-Merge Verification

After merging and deploying to VM:
```bash
ssh openclaw@20.124.180.8
cd /opt/global-sentinel
git pull origin main
pip install -r requirements.txt
sudo systemctl restart global-sentinel

# Wait one cycle (~60s)
sudo journalctl -u global-sentinel --since '2 minutes ago' --no-pager | grep -iE 'oil_regime\|broker_audit\|error\|warning'

# Verify scorecard includes new fields
sudo journalctl -u global-sentinel --since '2 minutes ago' --no-pager | grep 'v6_oil_regime'
```
