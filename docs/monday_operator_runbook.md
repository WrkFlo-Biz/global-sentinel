# Monday Morning Operator Runbook — 2026-03-10

## Context
- Weekend state: 71 day-trade positions, 21 medium-long positions
- 78 pending close orders queued for market open (66 day-trade, 12 medium-long)
- Oil regime module and broker audit being built by Codex over the weekend
- Exa AI exhausted, SerpAPI fallback active
- All times are Eastern Time (ET)

---

## 4:00 AM ET — Overnight Check

```bash
ssh openclaw@20.124.180.8
sudo journalctl -u global-sentinel --since '8 hours ago' --no-pager | tail -50
```

**Verify:**
- Cycles are completing (look for `cycle_complete` or `scorecard` entries)
- No crash loops (no repeated `systemd` restart messages)
- No kill switch activation (no `kill_switch triggered` messages)
- Rate limiter not saturated (no `rate_limit_exceeded` warnings)

**If service is down:**
```bash
sudo systemctl status global-sentinel
sudo journalctl -u global-sentinel --since '1 hour ago' --no-pager | tail -100
# Check for OOM, segfault, or Python traceback
sudo systemctl restart global-sentinel
```

**Check dashboard is live:**
```bash
curl -s http://localhost:8501/healthz && echo "Dashboard OK" || echo "Dashboard DOWN"
# If down:
sudo systemctl restart global-sentinel-dashboard
```

**Check Telegram bot:**
```bash
sudo systemctl status global-sentinel-telegram
sudo journalctl -u global-sentinel-telegram --since '1 hour ago' --no-pager | tail -20
```

---

## 6:00 AM ET — Pull Latest Code (if Codex patch merged)

```bash
cd /opt/global-sentinel
git fetch origin
git log --oneline origin/main..HEAD  # check if we're behind
git pull origin main
```

**If new code pulled:**
```bash
# Install any new dependencies
pip install -r requirements.txt
# Restart services to pick up changes
sudo systemctl restart global-sentinel
sudo systemctl restart global-sentinel-dashboard
# Verify restart
sudo journalctl -u global-sentinel --since '1 minute ago' --no-pager | tail -10
```

---

## 7:00 AM ET — Pre-Market Scan

```bash
cd /opt/global-sentinel
python3 scripts/ops/pre_market_scan.py
```

**Verify:**
- Oil futures price and overnight change (if WTI > $95, note for ELEVATED switch)
- Strategy triggers from overnight data
- Edge detector findings (cascade/divergence/lag signals)
- Any geopolitical developments from news feeds
- Chokepoint composite score (currently 0.07 — verify it's updating)

**Check oil price manually if scan fails:**
```bash
python3 -c "
from src.data.market_data import MarketData
md = MarketData()
oil = md.get_price('CL1')
print(f'WTI: \${oil:.2f}')
"
```

**If WTI > $95:**
- Note this for the 8:45 AM regime switch
- Consider switching to ELEVATED early
- Check chokepoint composite — if > 0.30, ELEVATED is warranted regardless of price

---

## 8:00 AM ET — Broker Order Audit

```bash
cd /opt/global-sentinel
python3 scripts/ops/run_broker_order_audit.py --dry-run
```

**Expected output (approximate):**
```
=== Broker Order Audit (DRY RUN) ===
Account: PA3F6696XKWK (Day Trade)
  pending_close: ~66 orders (WAIT)
  stale_open:     0-5 orders (WOULD CANCEL)
  duplicate:      0-2 orders (WOULD CANCEL newer)
  partial_fill:   0 orders
  rejected:       0-3 orders (WOULD CLEAR)
  position_leak:  0 positions

Account: PA36T8OFBNXB (Medium Long)
  pending_close: ~12 orders (WAIT)
  stale_open:     0-2 orders
  ...
```

**Decision points:**
- If stale_open > 0: proceed to cancel
- If duplicate > 0: proceed to cancel newer
- If partial_fill > 0: manually review each (see broker_cleanup_spec.md)
- If position_leak > 0: CRITICAL — investigate immediately

**If stale or duplicate orders found:**
```bash
python3 scripts/ops/run_broker_order_audit.py --execute --cancel-stale
```

**Re-verify after cleanup:**
```bash
python3 scripts/ops/run_broker_order_audit.py --dry-run
```
Confirm: stale_open=0, duplicate=0, only pending_close remaining.

---

## 8:30 AM ET — Check Oil Regime Module (if Codex patch deployed)

```bash
python3 -c "
from src.regime.oil_shock_regime import OilShockRegime
regime = OilShockRegime()
print(f'Module loaded: OK')
print(f'Config: {regime.config}')
" 2>&1
```

**If module not found:** Codex patch may not be merged yet. This is non-blocking — the system runs fine without it. Oil regime will be evaluated manually via pre-market scan.

**If module loads, test with current price:**
```bash
python3 -c "
from src.regime.oil_shock_regime import OilShockRegime
from src.data.market_data import MarketData
md = MarketData()
oil = md.get_price('CL1')
regime = OilShockRegime()
result = regime.evaluate(wti_price=oil)
print(f'WTI: \${oil:.2f}')
print(f'Regime: {result.regime}')
print(f'Triggers: {result.triggers}')
"
```

---

## 8:45 AM ET — Switch to ELEVATED (if warranted)

**Only switch if:**
- WTI > $95, OR
- Chokepoint composite > 0.30, OR
- Significant overnight geopolitical event

```bash
# Edit execution mode
nano /opt/global-sentinel/config/execution_mode.yaml
# Change: mode: ELEVATED

# Restart to pick up config change
sudo systemctl restart global-sentinel

# Verify mode switch in next cycle log (wait ~60s)
sudo journalctl -u global-sentinel --since '1 minute ago' --no-pager | grep -i 'mode\|regime\|elevated'
```

**If NOT switching to ELEVATED:** Leave mode as NORMAL. No action needed.

---

## 9:30 AM ET — Market Open

**Watch first 5 minutes for order execution:**
```bash
sudo journalctl -u global-sentinel -f | grep -iE 'fill|order|submit|block|reject'
```

**What to look for:**
- `fill` messages: pending close orders executing (expected: ~66 for day-trade)
- `reject` messages: broker rejecting orders (investigate immediately)
- `block` messages: guardrails blocking orders (expected for exceeded limits)
- `submit` messages: new orders being placed (expected after closes settle)

**Red flags:**
- Multiple `reject` messages in rapid succession — possible account issue
- No `fill` messages at all — possible broker connectivity issue
- `kill_switch triggered` — system halted, investigate cause

---

## 9:31 AM ET — Verify Flat Start (Day Trade Account)

```bash
cd /opt/global-sentinel
python3 scripts/ops/run_broker_order_audit.py --verify-flat
```

**Expected:** `0 positions, full buying power` for day-trade account.

**If positions remain:**
```bash
# Check which positions didn't close
python3 -c "
from src.broker.alpaca_adapter import AlpacaAdapter
adapter = AlpacaAdapter(account='day_trade')
positions = adapter.get_positions()
for p in positions:
    print(f'{p.symbol}: {p.qty} shares, \${p.market_value:.2f}, P&L: \${p.unrealized_pl:.2f}')
"
```

**If stuck positions:**
1. Check if close orders were rejected (look in audit log)
2. Manually submit market close orders:
```bash
python3 -c "
from src.broker.alpaca_adapter import AlpacaAdapter
adapter = AlpacaAdapter(account='day_trade')
# CAUTION: This closes a specific position. Verify symbol first.
# adapter.close_position('SYMBOL')
"
```

---

## 9:35 AM ET — First Trades

**Watch for new strategy ideas flowing through:**
```bash
sudo journalctl -u global-sentinel -f | grep -iE 'strategy|idea|submit'
```

**Expected:**
- StrategyEngine producing 15-25 ideas per cycle
- Ideas being filtered by guardrails
- Qualifying ideas being submitted as orders
- Orders being filled within minutes

**If no ideas flowing:**
- Check if strategies are enabled: `cat /opt/global-sentinel/config/war_strategies.yaml | grep enabled`
- Check if market data is flowing: look for `market_data` or `price` entries in logs
- Check if guardrails are blocking everything: look for `block` or `guardrail` entries

---

## 10:00 AM ET — First Review

```bash
cd /opt/global-sentinel
python3 -c "
from src.risk.exposure_book import ExposureBook
eb = ExposureBook()
snap = eb.snapshot()
print(f'=== Exposure Report ===')
print(f'Gross: {snap[\"combined\"][\"gross_exposure_pct\"]:.0%}')
print(f'Net: {snap[\"combined\"][\"net_exposure_pct\"]:+.0%}')
print(f'Positions: {sum(len(a[\"positions\"]) for a in snap[\"accounts\"].values())}')
print()
for acct_id, acct in snap['accounts'].items():
    print(f'Account {acct_id}: {len(acct[\"positions\"])} positions, gross={acct[\"gross_exposure_pct\"]:.0%}')
"
```

**Expected ranges:**
- Gross exposure: 20-80% (building up from flat)
- Net exposure: +10% to +50% (market-dependent)
- Positions: 5-30 (building up gradually)

**If gross > 100% within first 30 min:** Something is sizing too aggressively. Check strategy weights and position sizing config.

**Check scanner output:**
```bash
python3 -c "
from src.scanner.war_opportunity_scanner import WarOpportunityScanner
wos = WarOpportunityScanner()
discoveries = wos.latest_discoveries()
print(f'Discoveries this cycle: {len(discoveries)}')
for d in discoveries[:5]:
    print(f'  {d[\"symbol\"]}: {d[\"opportunity_type\"]} (score: {d[\"score\"]:.2f})')
"
```

---

## 12:00 PM ET — Midday Check

```bash
# Quick health check
sudo journalctl -u global-sentinel --since '30 minutes ago' --no-pager | tail -20

# Exposure snapshot
cd /opt/global-sentinel
python3 -c "
from src.risk.exposure_book import ExposureBook
eb = ExposureBook()
snap = eb.snapshot()
print(f'Gross: {snap[\"combined\"][\"gross_exposure_pct\"]:.0%}')
print(f'Net: {snap[\"combined\"][\"net_exposure_pct\"]:+.0%}')
print(f'Day trade positions: {len(snap[\"accounts\"].get(\"PA3F6696XKWK\", {}).get(\"positions\", []))}')
print(f'Medium long positions: {len(snap[\"accounts\"].get(\"PA36T8OFBNXB\", {}).get(\"positions\", []))}')
"

# Check P&L
python3 -c "
from src.broker.alpaca_adapter import AlpacaAdapter
for acct in ['day_trade', 'medium_long']:
    adapter = AlpacaAdapter(account=acct)
    info = adapter.get_account_info()
    print(f'{acct}: equity=\${float(info.equity):,.2f}, P&L today=\${float(info.equity) - float(info.last_equity):+,.2f}')
"
```

---

## 4:00 PM ET — Post-Session

```bash
cd /opt/global-sentinel

# Run broker audit
python3 scripts/ops/run_broker_order_audit.py --dry-run
```

**Verify:**
- Day-trade account: all positions closed (0 positions)
- Medium-long account: positions as expected (may hold overnight)
- No position leaks on either account
- No stale orders carried over

**Final P&L check:**
```bash
python3 -c "
from src.broker.alpaca_adapter import AlpacaAdapter
for acct in ['day_trade', 'medium_long']:
    adapter = AlpacaAdapter(account=acct)
    info = adapter.get_account_info()
    pnl = float(info.equity) - float(info.last_equity)
    print(f'{acct}: equity=\${float(info.equity):,.2f}, day P&L=\${pnl:+,.2f}')
"
```

**If day-trade positions not closed:**
- Check if close orders were submitted but not filled
- Manually close any remaining positions before after-hours trading begins
- Log the issue for investigation

---

## Emergency Procedures

### Kill Switch Activation
```bash
cd /opt/global-sentinel
python3 -c "
import json
with open('control/kill_switch.json', 'w') as f:
    json.dump({'active': True, 'reason': 'Manual activation', 'timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)'}, f, indent=2)
print('Kill switch ACTIVATED')
"
```

### Kill Switch Deactivation
```bash
python3 -c "
import json
with open('control/kill_switch.json', 'w') as f:
    json.dump({'active': False, 'reason': 'Manual deactivation', 'timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)'}, f, indent=2)
print('Kill switch DEACTIVATED')
"
```

### Force Close All Day-Trade Positions
```bash
python3 -c "
from src.broker.alpaca_adapter import AlpacaAdapter
adapter = AlpacaAdapter(account='day_trade')
adapter.close_all_positions()
print('All day-trade positions closed')
"
```

### Service Recovery
```bash
# Full restart of all services
sudo systemctl restart global-sentinel
sudo systemctl restart global-sentinel-dashboard
sudo systemctl restart global-sentinel-telegram

# Verify all running
sudo systemctl status global-sentinel global-sentinel-dashboard global-sentinel-telegram
```

### Daily Loss Halt Check
```bash
python3 -c "
from src.broker.alpaca_adapter import AlpacaAdapter
adapter = AlpacaAdapter(account='day_trade')
info = adapter.get_account_info()
pnl = float(info.equity) - float(info.last_equity)
halt_threshold = -3000
print(f'Day P&L: \${pnl:+,.2f}')
print(f'Halt threshold: \${halt_threshold:+,.2f}')
print(f'Status: {\"HALTED\" if pnl <= halt_threshold else \"OK\"}')
print(f'Remaining: \${pnl - halt_threshold:+,.2f}')
"
```

---

## Contact & Escalation
- Telegram alerts: automated via `/gs_` commands
- VM access: `ssh openclaw@20.124.180.8`
- Alpaca dashboard: https://app.alpaca.markets (paper accounts)
- Kill switch file: `/opt/global-sentinel/control/kill_switch.json`
