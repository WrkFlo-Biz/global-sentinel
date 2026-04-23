# global-sentinel

24/7 geopolitical risk intelligence and shadow-mode trading execution engine.

## Key directories
- src/execution/ — shadow order router, trade approval, broker bridges (IBKR, TastyTrade, Tradier, Alpaca)
- src/monitoring/ — Telegram bot, smart inference router, position monitors, crisis alerts
- src/risk/ — exposure book, VaR gate, manual veto MCP, scenario simulator
- src/ingestion/ — 15+ data bridges (FRED, BLS, EIA, CFTC, SEC, GDELT, NOAA, etc.)
- src/reports/ — OpenClaw role briefs, recommendation queue
- scripts/ops/ — daily thesis, market query, conditional orders, auto-commit
- config/ — execution mode, nemoclaw blueprint, openclaw role registry

## How to test
pytest -q tests/ -p no:cacheprovider

## Current work
- Permission tiers (Tier 0/1/2) defined in docs/permission-tiers.md
- OpenClaw demotion: stripping Tier-2 commands from Telegram handler
- Foundry client boundary: migrating LLM callers from direct Azure to orchestrator routing
- Broker keys in /opt/global-sentinel/.env (never commit)

## Integration
- Migrating to wrkflo-orchestrator for approval mediation and Foundry routing
- Telegram bots (mo2darkbot, mo2drkbot) being demoted to Tier 0/1 only
