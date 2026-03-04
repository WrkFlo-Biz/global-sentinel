# CAIO Briefing — Global Sentinel V4

You are preparing a CAIO (Chief AI Officer) briefing focused on model quality, drift, and improvement.

## Source of Truth
- Self-improvement loop outputs at `reports/improvement_proposals/`
- Correlation sanity checks at `logs/risk_checks/correlation-*.json`
- Outcome tracker results at `logs/risk_checks/outcome-*.json`
- OpenClaw-Research reports at `reports/openclaw_research/`

## Brief Format

### Model Quality
- Average confidence over last 24 hours
- Confidence trend (improving / stable / degrading)
- Stale data frequency and fallback mode activations

### Drift Detection
- Signal distribution changes detected
- Drift magnitude and affected components
- Correlation break alerts

### Self-Improvement Pipeline
- Pending improvement proposals
- Staging experiment results
- Candidate configs vs current config performance
- Proposals awaiting human promotion gate

### Replay & Backtest Results
- Recent replay scenarios run
- False positive / false negative rates
- Precision, recall, timing lag metrics

### Prompt & Policy Health
- Any prompt variant evaluations completed
- Hallucination resistance scores
- Uncertainty handling quality

### Safety Audit
- Last safety audit status
- Any violations detected
- Live order path checks (must be zero)

## Rules
- Focus on model and signal quality, not market signals
- Cite improvement proposal filenames and timestamps
- Highlight any auto-promotion attempts during CRISIS (should be zero)
- Separate verified metrics from preliminary signals
