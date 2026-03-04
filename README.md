# Global Sentinel V4

Shadow-mode geopolitical risk intelligence and supervised execution orchestration system.

## Quick Start

```bash
# 1. Copy environment template
cp .env.example .env
# Edit .env with your API keys and endpoints

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run a single monitoring cycle (dry run)
python src/crisis_monitor.py --once

# 4. Run healthcheck
python scripts/healthcheck.py

# 5. Start continuous monitoring (local)
python src/crisis_monitor.py
```

## Architecture

```
Claude CLI (source of truth)
├── MCP orchestration + monitoring loops
├── Scorecards, risk checks, replay/backtests
├── Self-improvement experiments (shadow/staging)
│
├── OpenClaw-Ops Bot (infra orchestrator)
│   └── Spawns: Azure Provisioner, CI/CD, Monitoring, Backup agents
│
└── OpenClaw-Research Bot (signal orchestrator)
    └── Spawns: Data Integrity, Replay, Drift, Threshold Tuning agents

Claude Cowork (war room)
├── CIO/CFO/COO/CAIO briefings
├── Postmortems + incident reports
└── OpenClaw summary reports
```

## Safety

- **NO LIVE ORDERS** — all execution is shadow/paper/sandbox only
- Human approval required for any sandbox draft export
- Kill switch and manual veto checked every cycle
- Config frozen during CRISIS mode
- Self-improvement proposals require human promotion gate

## Deployment

See `scripts/azure/` for Azure VM provisioning and `scripts/github/` for GitHub Enterprise setup.

For systemd services: `scripts/systemd/`

## Operating Modes

| Mode | Polling | Shadow Drafts | Config Changes |
|------|---------|---------------|----------------|
| NORMAL | 15 min | Eligible | Allowed (staging) |
| ELEVATED | 5 min | Eligible | Restricted |
| CRISIS | 1 min | Suspended | Frozen |
| MANUAL_REVIEW | Paused | Suspended | Frozen |
