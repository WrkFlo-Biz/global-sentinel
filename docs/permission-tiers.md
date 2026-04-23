# Permission Tiers

## Purpose

This note classifies the current Global Sentinel action surface into the three
permission tiers needed for the new architecture:

- Tier 0: observe
- Tier 1: advise
- Tier 2: guarded side effects

The target boundary follows `docs/architecture-delta-gs-view.md:43-85` and
`docs/openclaw-demotion.md`: OpenClaw stays in Tier 0-1, while Tier 2 actions
move behind `wrkflo-orchestrator` approval transport and durable audit.

## Tier 0: Observe

Tier 0 is read-only access to state, reports, and telemetry. It must not change
runtime files, control files, execution mode, or broker state.

### Current Tier-0 examples

| Surface | Current code | Why it is Tier 0 |
| --- | --- | --- |
| Telegram read commands | `src/monitoring/telegram_command_handler.py:352-395`, `src/monitoring/telegram_command_handler.py:484-611` | `/status`, `/gss`, `/portfolio`, `/orders`, `/alerts`, and `/config` read dashboard state only. |
| Dashboard read endpoints | `server.py:588-602`, `server.py:655-663`, `server.py:861-870` | Read-only API surfaces expose execution config, pending orders, and control status. |
| OpenClaw role brief context loading | `src/reports/openclaw_role_briefing.py:82-133` | Reads scorecards, operational reports, and control flags without changing system state. |

### Target rule

Tier 0 can remain broadly available to dashboards, bots, and operator UIs
because it does not mutate runtime state.

## Tier 1: Advise

Tier 1 may write reports, queue advisories, or produce decision-support
artifacts, but it must not change execution state, approval state, broker
state, or repository state.

### Current Tier-1 examples

| Surface | Current code | Why it is Tier 1 |
| --- | --- | --- |
| OpenClaw role briefs | `src/reports/openclaw_role_briefing.py:54-80` | Writes advisory artifacts with explicit paper-only safety fields. |
| OpenClaw recommendation queue | `src/reports/openclaw_recommendation_queue.py:27-62` | Queues advisories with `manual_approval_required` and `live_execution_forbidden`. |
| Daily thesis output | `scripts/ops/daily_thesis_generator.py:98-134` | Produces and stores a thesis plus a Telegram message; no broker or control mutation occurs in this script. |
| Market query artifact | `scripts/ops/market_query.py:133-152` | Writes `last_market_query.json` as decision support only. |
| OpenClaw data feed | `scripts/ops/openclaw_data_feed.py:180-275` | Writes outbound summaries and JSON snapshots for downstream consumption. |

### Tier-1 boundary violation to fix

`scripts/agent_factory.py:360-403` writes proposal-review artifacts that set
`requires_human_approval: false` and `execution_enabled: true`. That payload is
advisory-shaped but not advisory-safe. It should either become explicit Tier 1
advice or move into Tier 2 with approval mediation.

### Target rule

Tier 1 is the highest tier OpenClaw should retain inside this repo.

## Tier 2: Guarded Side Effects

Tier 2 includes any action that changes runtime control state, approval state,
broker state, or the repository itself. Tier 2 must be operator-identified,
durably audited, and mediated by an orchestrator approval boundary.

### Tier-2A: Control-plane mutation

| Surface | Current code | Current mutation | Approval gap | Target gate |
| --- | --- | --- | --- | --- |
| Execution mode API | `server.py:605-624` | Rewrites `config/execution_mode.yaml`. | Auth can be required via `GS_DASHBOARD_API_KEY` (`server.py:28-48`), but there is no explicit approval workflow. | Require orchestrator-issued approval for mode changes. |
| Telegram mode command | `src/monitoring/telegram_command_handler.py:396-423` | Calls `/api/execution-mode` directly from chat. | Chat authorization is not the same as an approval record. | Remove from bot surface or proxy through orchestrator approval. |
| Telegram approval/reject command | `src/monitoring/telegram_command_handler.py:456-482`, `server.py:629-650` | Writes `control/pending_approval_{strategy}.json`. | Direct file write after chat action; no external approval service owns the verdict. | Make orchestrator the approval source of truth. |
| Kill/veto command handler | `src/monitoring/telegram_command_handler.py:424-454`, `src/monitoring/telegram_command_handler.py:662-704` | Writes `control/kill_switch.json` and `control/manual_veto.json`. | Only a local YES confirmation is required. | Replace with orchestrator-mediated control actions. |
| Dashboard kill/veto APIs | `server.py:833-859` | Directly rewrites control files. | API auth is optional by env and still does not create an approval record. | Require orchestrator approval tokens and durable action IDs. |
| Manual veto MCP | `src/risk/manual_veto_mcp.py:112-142`, `src/risk/manual_veto_mcp.py:145-176`, `src/risk/manual_veto_mcp.py:209-226` | Exposes tool calls that set or clear veto/kill flags. | No explicit approval hook; tool invocation is the authority. | Route these tool actions through orchestrator approval. |
| Refresh signal | `src/monitoring/telegram_command_handler.py:613-627` | Sends `SIGUSR1` to the crisis monitor. | No approval gate. | Treat as a guarded control action, not a read command. |

### Tier-2B: Broker or paper/shadow execution

| Surface | Current code | Current mutation | Approval gap | Target gate |
| --- | --- | --- | --- | --- |
| Shadow router | `src/execution/shadow_order_router.py:109-455` | Creates intents, runs checks, requests approval, and submits through broker adapters. | This is an execution path, not observation-only, despite the `shadow only` header. | Require orchestrator-issued approval context for all submissions. |
| Trade approval workflow | `src/execution/trade_approval.py:275-343` | Auto-approves when approval is disabled, below threshold, Telegram config is missing, send fails, or timeout expires with auto-execute enabled. | Fails open in several branches. | Fail closed unless orchestrator returns an explicit approved verdict. |
| Conditional order engine | `scripts/ops/conditional_order_engine.py:567-579` | Calls `route_and_execute()` for options routing. | No orchestrator approval boundary is visible at the call site. | Pass only orchestrator-approved intents into execution. |
| Multi-broker router | `src/execution/multi_broker_router.py:909-1035` | Selects broker, executes, and can fail over across brokers. | No external approval transport. | Keep as GS execution machinery, but only after orchestrator approval. |
| IBKR direct order bridge | `src/execution/ibkr_bridge.py:199-288` | Places and cancels orders against IBKR. | Direct callable order placement. | Restrict to orchestrator-approved execution runs only. |
| Tastytrade direct order bridge | `src/execution/tastytrade_mcp_bridge.py:113-201` | Places equity/option orders and cancels orders directly. | Direct callable order placement. | Restrict to orchestrator-approved execution runs only. |
| Tradier sandbox adapter | `src/execution/tradier_sandbox_adapter.py:111-164` | Submits, cancels, and replaces orders, with shadow-mode enforcement in the adapter. | Safer than live paths, but still a side-effecting broker surface. | Keep inside Tier 2 with approval and audit. |

### Tier-2C: Repo mutation

## Current vs Target State

### Current

- Tier 0 and Tier 1 are already present and reasonably separable.
- Tier 2 is spread across Telegram commands, dashboard write APIs, MCP tools,
  broker routers, and scripts.
- Several Tier-2 paths fail open or rely only on local chat/API authorization
  rather than a durable approval service.

### Target

- Tier 0 remains broadly readable.
- Tier 1 remains artifact- and advice-only.
- Tier 2 moves behind one orchestrator approval transport with:
  - operator identity
  - approval scope
  - action/run ID
  - durable audit record
  - fail-closed behavior on transport or approval errors

## Migration Plan

1. Reclassify bot surfaces.
   - Remove Tier-2 commands from `TelegramCommandHandler`.
   - Keep chat surfaces to Tier 0 reads and optional Tier 1 advisory delivery.

2. Put all control mutations behind one approval transport.
   - `set_execution_mode`, kill/veto writes, and approval-file writes should no
     longer be the terminal authority.
   - They should consume an orchestrator verdict payload instead.

3. Make execution approval fail closed.
   - In `trade_approval.py`, missing config, send failures, and timeouts should
     block submission unless there is an explicit orchestrator-approved verdict.

4. Require approved execution context at submission boundaries.
   - `shadow_order_router.py`, `multi_broker_router.py`, broker bridges, and
     scripts like `conditional_order_engine.py` should accept only approved
     intents, not decide approval locally.

5. Isolate repo mutation.
   - Unattended repo mutation should not exist in runtime behavior.
   - Any future repo mutation job should be an explicit manual workflow with
     operator approval.

## Verification Commands

Use these commands after migration work:

```bash
cd /home/moses/projects/global-sentinel

rg -n "/api/execution-mode|/api/telegram/approve|/api/control/kill-switch|/api/control/veto|set_manual_veto|set_kill_switch|clear_all_flags" \
  server.py src scripts

rg -n "request_approval|auto_approved|auto-executing|route_and_execute\\(|place_order\\(|delete_order\\(|git push" \
  src scripts
```

Expected end state:

- Tier-2 control mutations should no longer be reachable directly from
  OpenClaw-linked bots or local dashboard endpoints without orchestrator
  mediation.
- Execution code may still exist in GS, but it should require an external
  approval context rather than authorizing itself.
- Repo mutation should not remain as an unattended runtime behavior.
