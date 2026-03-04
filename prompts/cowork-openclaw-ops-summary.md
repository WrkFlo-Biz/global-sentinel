# OpenClaw-Ops Summary — Global Sentinel V4

You are generating an OpenClaw-Ops (Infrastructure + Reliability) summary report.

## Source of Truth
- Agent factory logs at `logs/events/agent-*.json`
- Healthcheck outputs
- System metrics (heartbeat, disk, memory, uptime)
- Incident logs

## Report Format

### Infrastructure Health
- VM status (control-plane, workers if any)
- Disk utilization and trends
- Memory utilization
- Network connectivity
- NTP/clock drift status

### Agent Pool Summary
| Agent Type | Active | Completed | Failed | Expired | Avg Latency |
|-----------|--------|-----------|--------|---------|-------------|
| | | | | | |

### Queue Status
- Ops queue depth
- Scaling recommendation (scale up / maintain / scale down)
- Failure rate trend

### Incidents (last 24 hours)
- Dead-letter queue entries
- Service restart events
- Failed healthchecks
- Control state changes

### Backup & Recovery
- Last backup timestamp
- Config snapshot status
- Recovery readiness

### Recommendations
- Infrastructure changes needed
- Capacity planning notes
- Maintenance windows suggested
