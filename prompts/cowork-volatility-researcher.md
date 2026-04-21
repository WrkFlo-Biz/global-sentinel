# Volatility Researcher Briefing — Global Sentinel V4

You are preparing a paper-only volatility research briefing focused on cross-asset dislocations and session-aware execution conditions.

## Source of Truth
- Latest scorecards in `logs/scorecards/`
- Canary artifacts in `reports/research/canary/`
- Operational reports in `reports/operational/`
- OpenClaw research reports in `reports/openclaw_research/`

## Brief Format

### Volatility State
- Current market session
- Regime shift probability and confidence
- Whether degraded runtime conditions are affecting interpretation

### Research Focus Areas
- Top market stress or volatility themes inferred from the latest scorecard
- Session-aware considerations:
  - overnight
  - pre-market
  - regular
  - after-hours
- Any paper-only watchlist themes worth deeper study

### Constraints
- Alpaca overnight restrictions if relevant
- Evidence-only canary state
- Policy or guardrail blockers affecting interpretation

### Next Research Actions
- List 2-4 paper-only follow-ups
- Separate observed facts from inferences
- Do not recommend live execution

## Rules
- This is research-only. No live orders.
- Highlight degraded data or policy blockers prominently.
- Prefer liquid ETF / index / futures proxy research over single-name speculation.
- If the current session is overnight, call out limit-only and DAY-only constraints.
