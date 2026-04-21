# Global Sentinel V4 — Claude Code Project Instructions

## Project Overview
Global Sentinel is a 24/7 shadow-mode geopolitical risk intelligence and supervised execution orchestration system.

**Doctrine: "Geopolitical arbitrage, not HFT."** Optimize for 2nd/3rd-order effects of macro events, not millisecond execution.

## SAFETY RULES
1. No single-source escalations — freshness quorum required.
2. Risk gate + manual veto + kill switch enforced every cycle.
3. Self-improvement may propose/test in shadow/staging but NEVER auto-promote to production during CRISIS mode.
4. Config freeze in CRISIS mode — no threshold/prompt changes without human approval.

## Architecture
- **Claude CLI**: Authoritative engine — MCP orchestration, monitoring loops, scorecards, risk checks, replay, infra automation, self-improvement experiments.
- **Claude Cowork**: War-room reporting console — executive briefings, scenario deep dives, postmortems, coordination summaries.
- **OpenClaw-Ops Bot**: Infrastructure + reliability orchestrator with dynamic subagent spawning.
- **OpenClaw-Research Bot**: Signal + model improvement orchestrator with dynamic subagent spawning.

## Operating Modes
| Mode | Polling | Alerting | Shadow Drafts | Config Changes |
|------|---------|----------|---------------|----------------|
| NORMAL | 15 min | On threshold breach | Eligible | Allowed (staging) |
| ELEVATED | 5 min | Every cycle | Eligible | Restricted |
| CRISIS | 1 min | Every cycle | Suspended | Frozen |
| MANUAL_REVIEW | Paused | Incident-only | Suspended | Frozen |

## Output Contract (every cycle)
Every monitoring cycle MUST produce: timestamp_utc, mode, regime_shift_probability, component_scores, confidence, evidence, data_freshness_status, threshold_values_used, risk_gate_status, manual_veto_status, kill_switch_status, fallback_mode_status, shadow_execution_eligible, hedge_draft (if any; shadow only).

## Key File Paths
- Config: `config/thresholds.yaml`, `config/assets_watchlist.yaml`
- Control: `control/manual_veto.json`, `control/kill_switch.json`
- Logs: `logs/events/`, `logs/scorecards/`, `logs/risk_checks/`, `logs/dead_letter/`
- Reports: `reports/flash/`, `reports/cio/`, `reports/cfo/`, `reports/coo/`, `reports/caio/`

## Day-0 Core Team (Personas)
1. Data Engineer (CLI focus)
2. Macro Strategist (CLI/Cowork focus)
3. Risk Officer (CLI focus)
4. Chief of Staff (Cowork focus)
5. OpenClaw-Ops Supervisor
6. OpenClaw-Research Supervisor

## Development Rules
- Always check `control/manual_veto.json` and `control/kill_switch.json` before any shadow draft.
- Log exact threshold values used in every scorecard.
- Dead-letter queue malformed packets to `logs/dead_letter/`.
- Correlation sanity check every 6 hours.
- Hysteresis on mode transitions to prevent flapping.
- Confidence penalties for stale/conflicting/fallback data.
