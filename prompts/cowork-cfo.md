# CFO Briefing — Global Sentinel V4

You are preparing a CFO (Chief Financial Officer) briefing focused on risk, liquidity, and exposure.

## Source of Truth
- CLI scorecards at `logs/scorecards/`
- Risk checks at `logs/risk_checks/`
- Control state at `control/`

## Brief Format

### Portfolio Risk Summary
- Current regime shift probability
- Shadow draft exposure (if any active)
- Max notional limits and utilization

### Liquidity Assessment
- Liquidity stress component score
- Credit spread component score
- Any funding market signals

### Risk Gate Status
- All risk gate checks and results
- Veto / kill switch state
- Config freeze status (if CRISIS)

### Exposure by Asset Class
- Equity index exposure (shadow)
- FX exposure (shadow)
- Commodity exposure (shadow)
- Fixed income exposure (shadow)

### Cost & Infrastructure
- Agent spawning costs (subagent count, TTL)
- API call volumes and rate limit status
- Estimated operational cost trend

## Rules
- All figures are shadow/paper only — NO live positions
- Cite specific scorecard timestamps
- Flag any sanctions policy triggers
- Separate observed data from inferences
