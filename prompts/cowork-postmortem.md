# Postmortem Template — Global Sentinel V4

You are generating a postmortem report for a Global Sentinel crisis event or incident.

## Source of Truth
- Scorecards from the incident window at `logs/scorecards/`
- Flash memos from `reports/flash/`
- Risk check logs at `logs/risk_checks/`
- Dead-letter items at `logs/dead_letter/`
- Control state changes during the period

## Postmortem Format

### Incident Summary
- **Incident ID:** (auto-generated or manual)
- **Duration:** Start time — End time (UTC)
- **Peak Mode:** (ELEVATED / CRISIS / MANUAL_REVIEW)
- **Peak Regime Probability:** X.XXX
- **Impact:** (what was affected — shadow drafts, data quality, system health)

### Timeline
Chronological sequence of events with timestamps:
- T+0: Initial trigger
- T+N: Mode transition(s)
- T+N: Control actions (veto, kill switch)
- T+N: Resolution / mode downgrade

### Root Cause Analysis
- What triggered the event?
- Was it a true regime shift or a false positive?
- Were there data quality issues?
- Did correlation sanity checks flag anything?

### System Response Assessment
- Did mode transitions occur at appropriate thresholds?
- Did hysteresis prevent flapping?
- Were confidence penalties applied correctly?
- Did risk gates function as expected?

### What Went Well
- (list)

### What Could Be Improved
- (list)

### Action Items
| # | Action | Owner | Priority | Status |
|---|--------|-------|----------|--------|
| 1 | | | | |

## Rules
- Recommend only — do not apply changes
- Cite specific scorecard files and timestamps
- Separate facts from interpretations
- Note any data gaps that affected analysis
