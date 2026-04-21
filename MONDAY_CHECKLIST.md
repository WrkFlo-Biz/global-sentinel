# Monday March 10 Pre-Market Checklist

## Pre-Market (run by 9:00 AM ET)

- [ ] All bridges returning fresh data (especially options_greeks)
  ```
  ssh openclaw@20.124.180.8 'export $(grep -v "^#" /opt/global-sentinel/.env | xargs) && cd /opt/global-sentinel && python3 -c "from src.monitoring.crisis_monitor import CrisisMonitor; cm = CrisisMonitor(\"/opt/global-sentinel\"); print(cm._poll_all_bridges())"'
  ```
- [ ] Regime scorer components all > 0
- [ ] Paper accounts: check equity and buying power
  ```
  # Day trade: PA3F6696XKWK (~$100K)
  # Medium-long: PA36T8OFBNXB (~$500K)
  ```
- [ ] Kill switch tested (set kill_switch=true then false)
  ```
  ssh openclaw@20.124.180.8 'cat /opt/global-sentinel/control/kill_switch.json'
  ```
- [ ] Telegram bot responding
- [ ] 3 canary timers running green
  ```
  ssh openclaw@20.124.180.8 'systemctl status global-sentinel-* --no-pager | grep -E "Active:|global-sentinel"'
  ```
- [ ] Pre-trade controls config loaded (first_week_conservative)
  ```
  ssh openclaw@20.124.180.8 'cat /opt/global-sentinel/config/live_trading_guardrails.yaml | head -20'
  ```
- [ ] Human approval mode: ENABLED (require_human_approval=true)
- [ ] Operating mode: ELEVATED
- [ ] Multi-backend comparison: running (artifact-only)
  ```
  ssh openclaw@20.124.180.8 'ls -lt /opt/global-sentinel/reports/research/operational/ | head -5'
  ```

## During Session

- [ ] Monitor every trade idea before approving
- [ ] Check regime score every 30 minutes
- [ ] If daily loss > 1%: pause and review
- [ ] If daily loss > 2%: auto-halt should trigger
- [ ] If any bridge goes stale: check immediately

## Post-Session (4:00 PM ET)

- [ ] Run TCA shadow report
- [ ] Compare paper vs live fills
- [ ] Review all executed trades vs trade ideas
- [ ] Check P&L reconciliation
- [ ] Document lessons for Tuesday

## Emergency Commands

```bash
# Kill switch ON (halt everything):
ssh openclaw@20.124.180.8 'echo "{\"kill_switch\": true, \"reason\": \"manual halt\", \"set_by\": \"moses\", \"set_at\": \"$(date -u +%FT%TZ)\"}" > /opt/global-sentinel/control/kill_switch.json'

# Kill switch OFF:
ssh openclaw@20.124.180.8 'echo "{\"kill_switch\": false}" > /opt/global-sentinel/control/kill_switch.json'

# Manual veto (stop new trades):
ssh openclaw@20.124.180.8 'echo "{\"manual_veto\": true, \"reason\": \"review needed\"}" > /opt/global-sentinel/control/manual_veto.json'

# Check positions:
ssh openclaw@20.124.180.8 'export $(grep -v "^#" /opt/global-sentinel/.env | xargs) && cd /opt/global-sentinel && python3 -c "from src.execution.alpaca_paper_adapter import AlpacaPaperAdapter; import os; a=AlpacaPaperAdapter(api_key=os.environ[\"ALPACA_API_KEY\"],api_secret=os.environ[\"ALPACA_SECRET_KEY\"]); print(len(a.list_positions()),\"positions\")"'
```
