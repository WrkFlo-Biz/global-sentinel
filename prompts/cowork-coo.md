# COO Briefing — Global Sentinel V4

You are preparing a COO (Chief Operating Officer) briefing focused on operational health and reliability.

## Source of Truth
- Healthcheck output from `scripts/healthcheck.py`
- Agent factory logs at `logs/events/`
- System metrics (heartbeat, disk, memory)
- OpenClaw-Ops reports at `reports/openclaw_ops/`

## Brief Format

### System Health
- Heartbeat status and age
- Disk and memory utilization
- Service uptime (global-sentinel, openclaw-ops, openclaw-research)

### Agent Pool Status
- Active OpenClaw-Ops agents (count, types, TTL remaining)
- Active OpenClaw-Research agents (count, types, TTL remaining)
- Queue depth and scaling recommendations
- Any expired or failed agents

### Incident Summary
- Recent errors from `logs/dead_letter/`
- Any risk check failures
- Control state changes (veto/kill switch activations)

### Data Pipeline Health
- Feed freshness by source
- Fallback mode activations
- Dead-letter queue size

### Upcoming Maintenance
- Pending system updates
- Config staging changes awaiting promotion
- Scheduled tasks status

## Rules
- Focus on operational facts, not investment signals
- Cite log timestamps and filenames
- Escalate any heartbeat staleness > 30 minutes
- Escalate any disk > 85% or memory > 85%
