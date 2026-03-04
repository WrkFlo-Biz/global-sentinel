# CIO Briefing — Global Sentinel V4

You are preparing a CIO (Chief Investment Officer) briefing from Global Sentinel CLI outputs.

## Source of Truth
- CLI logs and scorecards at `logs/scorecards/`
- Flash memos at `reports/flash/`
- Risk checks at `logs/risk_checks/`
- Control state at `control/manual_veto.json` and `control/kill_switch.json`

## Brief Format

### Situation Assessment
- Current operating mode (NORMAL / ELEVATED / CRISIS / MANUAL_REVIEW)
- Regime shift probability and trend (rising / stable / falling)
- Confidence level and any penalties applied

### Key Signals
- Top 3 component scores driving the composite
- Evidence items from latest cycle
- Any correlation breaks or anomalies flagged

### Risk Posture
- Risk gate status
- Shadow draft eligibility
- Manual veto / kill switch status

### Data Quality
- Freshness status of each signal source
- Fallback mode status
- Number of stale sources and impact on confidence

### Recommendations
- Whether current mode is appropriate
- Any regime transition expected in next 1-6 hours
- Shadow draft considerations (if eligible)

## Rules
- Separate "Observed Facts" from "Inferences"
- Cite file names and timestamps for all data points
- Highlight uncertainty and data gaps prominently
- Do NOT recommend live execution — shadow only
- Do NOT modify thresholds or configs
