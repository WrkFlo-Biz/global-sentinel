# SHOCK REGIME OPERATOR PLAYBOOK — Monday 2026-03-10

## TONIGHT (NOW)
- [x] Oil regime module deployed and active
- [x] 13/15 strategies firing
- [ ] Verify SHOCK regime in scorecard
- [ ] Watch China open on TradingView (Shanghai Composite, Hang Seng)
- [ ] Watch crude oil futures (CL1! on TradingView)

## 2:00 AM ET — All code deployed
ssh openclaw@20.124.180.8
python3 -c "
import json, glob
sc = json.load(open(sorted(glob.glob('/opt/global-sentinel/logs/scorecards/scorecard_*.json'))[-1]))
print('Oil regime:', sc.get('v6_oil_regime'))
print('Strategies:', sc.get('v6_strategy_summary', {}).get('active_count'))
print('Ideas:', len(sc.get('v6_strategy_ideas', [])))
"
# MUST show: regime=SHOCK, strategies>=15, ideas>=30

## 3:00 AM ET — Europe pre-market
# Watch: TTF gas, Brent futures, STOXX 50 futures
# System should generate europe_pre_open ideas
sudo journalctl -u global-sentinel -f | grep -i 'europe\|EZU\|EWG'

## 4:00 AM ET — US futures open
# Watch: ES futures, NQ futures, CL futures
# System should generate us_premarket_gap ideas

## 7:00 AM ET — Pre-market scan
python3 scripts/ops/pre_market_scan.py

## 8:00 AM ET — Broker audit
python3 scripts/ops/run_broker_order_audit.py --dry-run
# Cancel any stale/duplicate orders
python3 scripts/ops/run_broker_order_audit.py --execute --cancel-stale

## 8:45 AM ET — Switch to ELEVATED mode
# Edit config/execution_mode.yaml → mode: ELEVATED
sudo systemctl restart global-sentinel

## 9:30 AM ET — MARKET OPEN
# Maximum focus. Watch:
sudo journalctl -u global-sentinel -f | grep -iE 'submit|fill|block|reject|idea'
# First 5 minutes: close orders fill, then new positions open

## 9:31 AM — Verify flat start
python3 scripts/ops/run_broker_order_audit.py --verify-flat

## 9:35-10:00 AM — First wave of trades
# Oil momentum, gap persistence, airlines short
# Watch P&L:
python3 -c "
from src.risk.exposure_book import ExposureBook
eb = ExposureBook()
print(eb.format_telegram())
"

## EMERGENCY
# Kill switch (instant):
curl -X POST http://localhost:8501/api/v6/kill-switch
# Or via Telegram: /gs_kill
