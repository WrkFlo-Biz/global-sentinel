# Foundry / Orchestrator Routing Adoption Plan

## Scope

This plan maps how `global-sentinel` should move from today's mixed local
dispatch model to `wrkflo-orchestrator` as the control plane for:

- Foundry role routing
- task submission and run tracking
- Tier-2 approval mediation
- channel and operator entry points that currently terminate inside GS

Inputs used for this pass:

- `/tmp/claude-sync-to-orchestrator.md`
- `docs/foundry-router-integration.md`
- `docs/migration-status.md`
- `docs/openclaw-demotion.md`
- current GS source under `src/`, `scripts/`, `dashboard/`, and `server.py`
- current orchestrator source under
  `/home/moses/projects/wrkflo-orchestrator/src/wrkflo_orchestrator`

## Current State Summary

- GS already has one intended Foundry boundary in
  `src/inference/foundry_client.py`.
- Active GS callers already using that boundary:
  - `scripts/ops/market_query.py`
  - `scripts/ops/daily_thesis_generator.py`
  - deprecated compatibility shim: `src/monitoring/smart_inference_router.py`
- `wrkflo-orchestrator` already exposes live read and task surfaces for:
  - `/v1/foundry/roles`
  - `/v1/tasks`
  - `/v1/runs`
  - `/v1/tasks/history`
  - `/v1/workers`
  - `/v1/projects`
  - `/v1/workspace/*`
- The orchestrator codebase already contains the core Foundry pieces:
  - `foundry.py`: role map, request envelope, policy annotations
  - `foundry_client.py`: HTTP client to Azure Foundry
  - `service.py`: approval-token verification and task/run API
- The live and source orchestrator still do **not** expose the GS inference
  contract yet:
  - `src/wrkflo_orchestrator/service.py` has no `/v1/inference` route
  - live `POST http://127.0.0.1:8100/v1/inference` currently returns `404`
- GS still owns major local dispatch and control authorities:
  - `scripts/agent_factory.py` local queue, worker loop, and `OpenClawStateDB`
  - `src/monitoring/telegram_command_handler.py` long-poll chat ingress plus a
    direct Anthropic free-form chat path
  - `src/bridges/openclaw_research_bridge.py` Telegram relay and reply polling
  - `dashboard/api/server.py`, `server.py`, `scripts/ops/gs_control.py`, and
    `src/risk/manual_veto_mcp.py` as local Tier-2 mutators
  - `src/execution/trade_approval.py` as a GS-owned Telegram approval loop

## Current GS Dispatch Entry Points

| Entry point | Current dispatch pattern | Why it blocks the target model | Target orchestrator posture |
| --- | --- | --- | --- |
| `src/inference/foundry_client.py` | Builds a GS envelope and posts to `ORCHESTRATOR_URL`, with direct Azure fallback on request failure. | The client contract exists, but the orchestrator runtime does not yet serve `/v1/inference`. | Keep this as the single GS inference boundary, but back it with a real orchestrator inference endpoint. |
| `scripts/ops/market_query.py` | Planner-style synchronous inference through `send_request(...)`, plus local reads of `control/manual_veto.json` and `control/kill_switch.json`. | The role mapping is good, but the operating-context read path still depends on GS-local control files. | Continue using the shared inference boundary; move control-state reads to an orchestrator-backed snapshot. |
| `scripts/ops/daily_thesis_generator.py` | Summarizer-style synchronous inference through `send_request(...)`, plus local control-file reads. | Same gap as `market_query.py`. | Continue using the shared inference boundary; read orchestrator-backed control state. |
| `src/monitoring/smart_inference_router.py` | Deprecated shim that classifies prompts and forwards into `foundry_client.send_request(...)`. | It preserves an old routing surface even though in-tree callers are gone. | Leave it as compatibility-only until external callers are migrated, then remove it. |
| `src/monitoring/telegram_command_handler.py` and `src/monitoring/telegram_bot_manager.py` | `getUpdates` long polling, free-form direct Anthropic chat, and Tier-2 command stubs that only print an approval message. | GS still owns Telegram ingress and one direct model-provider bypass. | Reduce to a channel adapter that submits orchestrator tasks or orchestrator-backed inference requests. |
| `src/bridges/openclaw_research_bridge.py` | Sends `/gs_research` messages over Telegram and polls `getUpdates` for replies. | Research routing still goes through a Telegram relay instead of an orchestrator task/run contract. | Replace with a read-only advisory intent such as `research.market_brief` or a GS-specific research task kind. |
| `scripts/agent_factory.py` | Owns a local priority queue, worker threads, seeding cadence, `OpenClawStateDB`, and execution-adjacent task kinds such as `strategy_executor` and `crypto_executor`. | It is still a GS-local orchestrator with its own runtime state and dispatch policy. | Demote to helper code behind orchestrator-owned tasks, or retire it after equivalent orchestrator tasks exist. |
| `dashboard/api/server.py`, `server.py`, `scripts/ops/gs_control.py`, `src/risk/manual_veto_mcp.py` | Direct file writes for `execution_mode.yaml`, `kill_switch.json`, `manual_veto.json`, and legacy approval files. | Tier-2 state authority still terminates in GS-local files. | Convert to guarded `POST /v1/tasks` callers or read-only surfaces. |
| `src/execution/trade_approval.py` | Telegram inline-button approval transport, callback polling, `/tmp/gs_pending_approvals`, and local audit mirroring. | The current beta approval model is front-loaded bearer-token submission, not suspended local waiting. | Require orchestrator-approved context on entry and remove GS-owned approval transport. |
| `scripts/ops/conditional_order_engine.py`, `scripts/agent_factory.py:run_strategy_executor`, `src/execution/shadow_order_router.py`, `src/execution/multi_broker_router.py` | Direct entry into the execution pipeline with local flags and GS-owned approval checks. | Execution-capable flows can still start inside GS before an orchestrator verdict exists. | Accept only orchestrator-approved execution context or guarded task payloads. |

## Where Foundry / Orchestrator Routing Already Exists

### GS-side

- `src/inference/foundry_client.py` already normalizes:
  - `intent_type`
  - `target_role`
  - `operating_context`
  - `latency_class`
  - `trace_context`
  - `messages`
- `scripts/ops/market_query.py` already maps to `planner`.
- `scripts/ops/daily_thesis_generator.py` already maps to `summarizer`.
- `src/monitoring/smart_inference_router.py` already maps:
  - `simple -> summarizer`
  - `moderate -> planner`
  - `complex -> critic`

### Orchestrator-side

- `src/wrkflo_orchestrator/foundry.py` already defines:
  - roles: `planner`, `critic`, `executor`, `summarizer`, `embeddings`, `realtime`
  - `FoundryRequestEnvelope`
  - `FoundryRouter.route_request(...)`
  - execution-sensitive `policy_annotations`
- `src/wrkflo_orchestrator/foundry_client.py` already defines
  `FoundryClient.invoke(...)` for the GS request/response contract.
- `src/wrkflo_orchestrator/service.py` already exposes:
  - run creation and history via `/v1/tasks` and `/v1/runs/*`
  - approval-token verification for guarded tasks
  - live role discovery via `/v1/foundry/roles`
- `src/wrkflo_orchestrator/tool_workers.py` already provides a governed task
  shell for read, safe-dev, and guarded worker kinds.

## Missing Integration Points

### 1. Inference ingress gap

GS's shared Foundry client points at `/v1/inference`, but the orchestrator
service source and live runtime do not currently expose that route.

Consequence:

- the intended GS boundary exists only on the client side today
- synchronous GS inference still depends on Azure fallback if the orchestrator
  endpoint is unavailable or absent

### 2. No GS task kinds or intent registry

The orchestrator currently recognizes:

- workspace workflows
- GitHub status
- Azure VM status
- repo patch
- browser CDP inspect

It does **not** yet recognize GS-specific kinds such as:

- `research.market_brief`
- `research.proposal_review`
- `gs.control.execution_mode.set`
- `gs.control.kill_switch.set`
- `gs.control.manual_veto.set`
- `gs.trade.prepare_ticket`
- `gs.trade.execute_shadow`

Consequence:

- GS cannot yet move local dispatch behind `POST /v1/tasks`
- approval tokens cannot yet bind to meaningful GS `kind` values

### 3. No GS-side task client boundary

GS has a shared inference client, but no equivalent shared task client for:

- `POST /v1/tasks`
- `GET /v1/runs/{id}`
- `GET /v1/runs/{id}/history`

Consequence:

- task submission logic is scattered across local Telegram, CLI, API, and
  execution paths instead of entering through one GS-owned boundary

### 4. Local runtime/state authority still lives in GS

`scripts/agent_factory.py` and `src/core/openclaw_state_db.py` still own:

- queueing
- worker health
- task history
- follow-up dispatch
- execution-adjacent seeding cadence

Consequence:

- GS still behaves like its own control plane for OpenClaw-era flows
- orchestrator cannot become the durable source of truth while this loop
  remains authoritative

### 5. Tier-2 mutation still terminates in local files

The following files still act as terminal authorities:

- `config/execution_mode.yaml`
- `control/kill_switch.json`
- `control/manual_veto.json`
- `control/pending_approval_{strategy}.json`
- `control/pending_orders_{strategy}.json`
- `/tmp/gs_pending_approvals/*`

Consequence:

- approval audit and action authority are split between the orchestrator and GS
- readers still treat local files as canonical state

## Recommended Adoption Sequence

### Step 1. Close the synchronous inference contract gap first

Goal:

- make the existing GS Foundry boundary real before touching broader dispatch

Orchestrator touch points:

- `src/wrkflo_orchestrator/service.py`
- `src/wrkflo_orchestrator/foundry_client.py`
- `src/wrkflo_orchestrator/foundry.py`
- `tests/test_service.py`
- `docs/current-runtime-truth.md`

Work:

- add `POST /v1/inference` to `service.py`
- route the request through `FoundryRequestEnvelope` and `FoundryClient.invoke(...)`
- return the GS-facing `FoundryResponseEnvelope`
- preserve `/v1/foundry/roles` as the role-discovery surface

Exit criteria:

- `market_query.py` and `daily_thesis_generator.py` can hit the orchestrator
  without falling back to direct Azure
- live `POST /v1/inference` returns `200` for a simple planner/summarizer call

### Step 2. Finish active GS runtime callers on one inference boundary

Goal:

- remove direct provider calls from active GS runtime entry points

GS touch points:

- `src/monitoring/telegram_command_handler.py`
- `src/inference/foundry_client.py`
- `scripts/ops/market_query.py`
- `scripts/ops/daily_thesis_generator.py`
- `src/monitoring/smart_inference_router.py`

Work:

- replace the direct Anthropic free-form chat in
  `telegram_command_handler.py` with `send_request(...)`
- map that path to `planner`
- keep `market_query.py` on `planner`
- keep `daily_thesis_generator.py` on `summarizer`
- leave `smart_inference_router.py` in compatibility mode only until any
  out-of-tree callers are cut over

Exit criteria:

- no active GS runtime caller talks to Anthropic or Azure directly
- all app-path inference enters through `src/inference/foundry_client.py`

### Step 3. Introduce GS task kinds in the orchestrator

Goal:

- make `POST /v1/tasks` meaningful for GS advisory and guarded work

Orchestrator touch points:

- `src/wrkflo_orchestrator/service.py`
- `src/wrkflo_orchestrator/tool_workers.py` or a new
  `src/wrkflo_orchestrator/workers/global_sentinel_worker.py`
- `src/wrkflo_orchestrator/workers/registry.py`
- `docs/openclaw-demotion.md`
- `docs/task-history-ops.md`

Recommended first GS kinds:

- `research.market_brief`
- `research.proposal_review`
- `gs.control.execution_mode.set`
- `gs.control.kill_switch.set`
- `gs.control.manual_veto.set`
- `gs.trade.prepare_ticket`

Work:

- define the payload contracts
- bind them to `project="global-sentinel"` and `target="global-sentinel"`
- mark the control and execution kinds as guarded Tier-2 work
- ensure runs record requester, target, and idempotency metadata

Exit criteria:

- one read-only GS advisory task and one guarded GS control task can be
  created through `/v1/tasks`
- task history can be filtered meaningfully for `repo=global-sentinel`

### Step 4. Move GS advisory dispatch off the local OpenClaw loop

Goal:

- stop treating `scripts/agent_factory.py` as the primary scheduler/router

GS touch points:

- `scripts/agent_factory.py`
- `src/core/openclaw_state_db.py`
- `src/bridges/openclaw_research_bridge.py`
- `src/monitoring/telegram_bot_manager.py`
- `src/monitoring/telegram_command_handler.py`

Work:

- take the first vertical slice through an advisory path, not a guarded trade
  path
- preferred slice:
  - `research.market_brief`
  - or `research.proposal_review`
- make Telegram/OpenClaw entry points submit orchestrator tasks and receive
  `run_id`
- reduce `agent_factory.py` from control-plane owner to:
  - helper code invoked by orchestrator-backed tasks
  - or a temporary compatibility runner with no independent scheduling role
- demote `OpenClawStateDB` from authority to mirror-only audit, then remove it
  once the orchestrator is authoritative

Exit criteria:

- at least one advisory GS flow carries orchestrator `run_id` end to end
- `agent_factory.py` is no longer the only owner of dispatch cadence for that
  flow

### Step 5. Collapse Tier-2 control into guarded orchestrator tasks

Goal:

- replace GS-local control mutation with one approval-token submission path

GS touch points:

- `dashboard/api/server.py`
- `server.py`
- `scripts/ops/gs_control.py`
- `src/risk/manual_veto_mcp.py`
- `src/execution/trade_approval.py`

Orchestrator touch points:

- `src/wrkflo_orchestrator/service.py`
- GS task-kind registration from Step 3

Work:

- convert API, CLI, and MCP writers into one of:
  - read-only surfaces
  - thin orchestrator proxies that submit guarded tasks
  - removed legacy surfaces
- stop writing new authority to:
  - `execution_mode.yaml`
  - `kill_switch.json`
  - `manual_veto.json`
  - `pending_approval_{strategy}.json`
  - `pending_orders_{strategy}.json`
  - `/tmp/gs_pending_approvals/*`
- preserve a compatibility snapshot only for readers that still need a local
  cache during the transition

Exit criteria:

- a control change is represented by orchestrator `run_id` and approval `jti`,
  not by a direct GS-local file write
- `trade_approval.py` no longer owns Telegram approval transport

### Step 6. Gate execution-capable paths on orchestrator verdicts

Goal:

- ensure no trade-capable flow can begin from local GS state alone

GS touch points:

- `scripts/ops/conditional_order_engine.py`
- `scripts/agent_factory.py:run_strategy_executor`
- `src/execution/shadow_order_router.py`
- `src/execution/multi_broker_router.py`
- broker bridges under `src/execution/`

Work:

- require orchestrator-approved execution context on entry, such as:
  - `kind`
  - `target`
  - `run_id`
  - `approval_jti`
  - `issued_by`
  - `reason`
  - `exp`
- fail closed when that context is missing or mismatched
- move local control-file readers to an orchestrator-backed read model or a
  GS-local compatibility snapshot derived from orchestrator verdicts

Exit criteria:

- `strategy_executor`, conditional order routing, and manual execution paths
  cannot proceed without an orchestrator verdict
- GS is consuming orchestrator-backed control state rather than raw local files

### Step 7. Remove compatibility layers after the control plane is real

Goal:

- retire transitional shims only after the replacement path has been verified

Likely removals or demotions:

- `src/monitoring/smart_inference_router.py`
- `src/core/openclaw_state_db.py`
- legacy pending-approval files
- Telegram research relay logic in `openclaw_research_bridge.py`
- duplicate Tier-2 mutator surfaces in both `dashboard/api/server.py` and
  `server.py`

Exit criteria:

- GS no longer has two competing routing authorities
- OpenClaw/Telegram surfaces are adapters, not control-plane owners

## Recommended First Vertical Slices

Use this order to minimize migration risk:

1. `POST /v1/inference` for existing synchronous callers
   - `daily_thesis_generator.py`
   - `market_query.py`
   - `telegram_command_handler.py` free-form chat
2. One advisory task kind
   - `research.market_brief`
   - or `research.proposal_review`
3. One guarded control kind
   - `gs.control.kill_switch.set`
   - or `gs.control.execution_mode.set`
4. Only then start execution-gated task kinds
   - `gs.trade.prepare_ticket`
   - `gs.trade.execute_shadow`

This sequencing keeps the first wins in read-only or advisory paths before
touching live control or execution-capable flows.

## Risks And Guardrails

- Do **not** remove Azure fallback from `src/inference/foundry_client.py`
  before `/v1/inference` is live and verified.
- Do **not** delete local control-file readers until an orchestrator-backed read
  model or compatibility snapshot exists.
- Do **not** plan around a suspended-run `/approve` endpoint; the current beta
  approval model is front-loaded bearer-token submission on the initial
  `POST /v1/tasks`.
- Do **not** migrate `strategy_executor` or `conditional_order_engine` before
  GS guarded task kinds and approval-token semantics are in place.
- Do **not** leave `agent_factory.py` half-demoted; either keep it as a clearly
  bounded compatibility loop or move the owned slice fully behind the
  orchestrator.

## Minimum Success Condition For This Lane

This audit lane is complete when the following are all true:

- GS synchronous inference goes through a live orchestrator endpoint
- at least one advisory GS task kind is live on `/v1/tasks`
- at least one guarded GS control task kind is live on `/v1/tasks`
- GS no longer treats Telegram, local files, or `OpenClawStateDB` as the
  authoritative routing layer for the migrated slice
