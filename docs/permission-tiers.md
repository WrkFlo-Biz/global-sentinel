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
| Dashboard read endpoints | `dashboard/api/server.py:2986-3041` | Read-only API surfaces still expose execution config. The legacy `pending-orders` compatibility path is now read-only too, but it returns `approval_required` orchestrator approval guidance instead of treating local pending-order files as truth. |
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
| Telegram Tier-2 command stubs | `src/monitoring/telegram_command_handler.py:429-447`, `src/monitoring/telegram_command_handler.py:578-618` | These paths now only return `ORCHESTRATOR_APPROVAL_MESSAGE`; they are operator UX affordances, not current mutators. |

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
| Execution mode API | `dashboard/api/server.py:3003-3018`, `server.py:662-678` | No current mutation. These endpoints now return `approval_required` orchestrator approval guidance for `gs.control.execution_mode.set` and leave `config/execution_mode.yaml` untouched. | The dashboard/root API layer still stops at a guidance payload instead of submitting the guarded task itself. | Replace the guidance response with real guarded task submission once the UI/client can call the shared task client directly. |
| Telegram Tier-2 command stubs | `src/monitoring/telegram_command_handler.py:429-447`, `src/monitoring/telegram_command_handler.py:578-618` | No current mutation. These commands return an approval instruction string and act as a request surface only. | There is still no structured orchestrator task submission from the bot UX; the flow stops at human-readable orchestrator approval guidance. | Replace stubs with real orchestrator task creation or remove the commands entirely from the bot UX. |
| Legacy approval bridge APIs | `dashboard/api/server.py:3025-3041`, `server.py:684-700` | No current mutation. `POST /api/telegram/approve` is disabled, and `GET /api/pending-orders` returns a demoted compatibility payload with `approval_required` instead of reading local pending-order files. | The compatibility surfaces still point operators at orchestrator approval, but they are not first-class task-submit clients. | Keep these bridges demoted until callers migrate, then remove them instead of restoring GS-local approval authority. |
| Dashboard kill/veto APIs | `dashboard/api/server.py:4030-4042`, `dashboard/api/server.py:4997-5007`, `server.py:870-882` | No current mutation. These endpoints now return `approval_required` orchestrator approval guidance for `gs.control.kill_switch.set` and `gs.control.manual_veto.set`, including the v6 kill-switch mirrors. | API auth is no longer the authority, but the dashboard still needs a structured guarded-submit path instead of a human-readable instruction only. | Replace the guidance response with durable guarded task creation and audit correlation once the dashboard can submit through orchestrator directly. |
| Manual veto MCP | `src/risk/manual_veto_mcp.py:97-205` | No current mutation. MCP tool calls now return `approval_required` guidance and scoped orchestrator commands instead of writing local kill/veto files. | The tool surface still stops at orchestrator approval guidance rather than submitting a guarded task programmatically. | Keep MCP Tier 2 as a request surface only until it can call the guarded task client directly. |
| Localhost remote-control CLI | `scripts/ops/gs_control.py:56-63`, `scripts/ops/gs_control.py:147-196` | No current mutation for Tier-2 commands. `kill`, `unkill`, `veto`, `unveto`, and `mode` now print orchestrator approval guidance instead of mutating GS endpoints over localhost. | The CLI is still a compatibility wrapper around guidance text rather than a direct orchestrator task submitter. | Retire it or make it submit guarded orchestrator tasks directly instead of stopping at approval instructions. |

### Tier-2B: Broker or paper/shadow execution

| Surface | Current code | Current mutation | Approval gap | Target gate |
| --- | --- | --- | --- | --- |
| Shadow router | `src/execution/shadow_order_router.py:109-455` | Creates intents, runs checks, requests approval, and submits through broker adapters. | This is an execution path, not observation-only, despite the `shadow only` header. | Require orchestrator-issued approval context for all submissions. |
| Trade approval workflow | `src/execution/trade_approval.py:466-657` | Validates guarded approval context, builds one scoped `gs.trade.execute_shadow` payload, and submits it through `src/core/orchestrator_task_client.py`. Legacy Telegram trade approval transport and local pending-file resolution are disabled. | This boundary is now fail closed, but all callers still need to arrive with valid approval token context and stop expecting Telegram or local files to approve later. | Keep `trade_approval.py` as the fail closed orchestrator-mediated trade approval boundary and remove remaining legacy request surfaces as callers migrate. |
| `position_manager` close handoff | `src/execution/position_manager.py:147-342`, `src/execution/position_manager.py:430-497` | Does not auto-close by default. For would-close proposals it now emits `approval_required`, `kind`, `target`, `ticket_id`, `orchestrator_command`, and `orchestrator_handoff` metadata for `gs.trade.execute_shadow`. | The module now produces a proper orchestrator handoff, but downstream close execution still has to consume that metadata instead of treating `pending_manual_approval` as a GS-local approval source of truth. | Use the emitted trade-ticket metadata as the only close-review contract and keep actual broker submission behind orchestrator-approved execution runs. |
| Conditional order engine | `scripts/ops/conditional_order_engine.py:567-579` | Calls `route_and_execute()` for options routing. | No orchestrator approval boundary is visible at the call site. | Pass only orchestrator-approved intents into execution. |
| Multi-broker router | `src/execution/multi_broker_router.py:909-1035` | Selects broker, executes, and can fail over across brokers. | No external approval transport. | Keep as GS execution machinery, but only after orchestrator approval. |
| IBKR direct order bridge | `src/execution/ibkr_bridge.py:199-288` | Places and cancels orders against IBKR. | Direct callable order placement. | Restrict to orchestrator-approved execution runs only. |
| Tastytrade direct order bridge | `src/execution/tastytrade_mcp_bridge.py:113-201` | Places equity/option orders and cancels orders directly. | Direct callable order placement. | Restrict to orchestrator-approved execution runs only. |
| Tradier sandbox adapter | `src/execution/tradier_sandbox_adapter.py:111-164` | Submits, cancels, and replaces orders, with shadow-mode enforcement in the adapter. | Safer than live paths, but still a side-effecting broker surface. | Keep inside Tier 2 with approval and audit. |

### Tier-2C: Repo mutation

Current audit result: no unattended runtime git-mutation path was found in
`src/`, `scripts/ops/`, `src/execution/`, `src/strategies/`, or
`src/reports/`.

The only repo/admin mutation hit in this pass was bootstrap tooling under
`scripts/github/bootstrap_ghe.sh:26-27`, which can create a GitHub repo. That
is Tier 2, but it is manual/bootstrap tooling rather than part of the GS
runtime path.

| Surface | Current code | Current mutation | Approval gap | Target gate |
| --- | --- | --- | --- | --- |
| GitHub bootstrap tooling | `scripts/github/bootstrap_ghe.sh:26-27` | Can run `gh repo create` during setup/bootstrap. | Not a runtime path, but still a repo-admin side effect with no orchestrator mediation. | Keep manual-only, or move under an explicit orchestrator GitHub worker flow if reused operationally. |

## Current vs Target State

### Current

- Tier 0 and Tier 1 are already present and reasonably separable.
- Tier 2 request surfaces are still spread across Telegram command stubs,
  dashboard/CLI/MCP guidance surfaces, broker routers, and scripts.
- The highest-risk dashboard approval/control endpoints no longer mutate local
  files directly. They now return `approval_required` orchestrator approval
  guidance, and `pending-orders` is demoted to a compatibility payload.
- `trade_approval.py` is now a fail closed, orchestrator-mediated trade approval
  boundary for scoped `gs.trade.execute_shadow` submissions.
- `position_manager.py` now emits orchestrator approval handoff metadata for
  close proposals, but downstream execution still needs to consume that handoff
  consistently.
- The remaining Tier-2 gap is that several request surfaces still stop at
  guidance instead of creating guarded orchestrator tasks directly.

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
   - Keep `TelegramCommandHandler` Tier-2 commands as temporary stubs only.
   - Replace them with real orchestrator task submission or remove them from the bot UX.
   - Keep chat surfaces to Tier 0 reads and optional Tier 1 advisory delivery.

2. Put all control mutations behind one approval transport.
   - `set_execution_mode`, kill/veto endpoints, and the legacy approval bridge
     surfaces should remain demoted compatibility layers, not the terminal
     authority.
   - Dashboard, CLI, and MCP request surfaces should eventually submit guarded
     orchestrator tasks directly instead of stopping at orchestrator approval
     guidance.

3. Make execution approval fail closed.
   - `trade_approval.py` now fails closed and submits one guarded trade task
     through the orchestrator task client.
   - Do not reintroduce Telegram callback polling, local pending files, or any
     late-binding trade approval path that bypasses orchestrator mediation.

4. Require approved execution context at submission boundaries.
   - `position_manager.py` now emits a trade-ticket handoff; downstream close
     execution should use that orchestrator handoff instead of local manual
     approval state.
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
  server.py dashboard/api/server.py src scripts

rg -n "request_approval|auto_approved|auto-executing|route_and_execute\\(|place_order\\(|delete_order\\(|gh repo create|git push" \
  src scripts
```

Expected end state:

- Tier-2 control mutations should no longer be reachable directly from
  OpenClaw-linked bots or local dashboard endpoints without orchestrator
  mediation.
- Telegram Tier-2 bot commands may still exist as stubs, but they should not
  mutate local state themselves.
- Execution code may still exist in GS, but it should require an external
  approval context rather than authorizing itself.
- Repo mutation should not remain as an unattended runtime behavior.
