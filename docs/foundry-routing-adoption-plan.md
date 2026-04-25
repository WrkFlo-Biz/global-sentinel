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
- GS also now has a shared task/run boundary in
  `src/core/orchestrator_task_client.py` for:
  - `POST /v1/tasks`
  - `GET /v1/runs/{id}`
  - `GET /v1/runs/{id}/history`
  - additive guarded helpers that standardize `project`, `kind`, `target`,
    requester identity, and approval-context fields
- Active GS callers already using that boundary:
  - `scripts/ops/market_query.py`
  - `scripts/ops/daily_thesis_generator.py`
  - `src/monitoring/telegram_command_handler.py`
  - deprecated compatibility shim: `src/monitoring/smart_inference_router.py`
- `wrkflo-orchestrator` source already exposes the core inference, read, and
  task surfaces for:
  - `/v1/inference`
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
  - `service.py`: `/v1/inference`, approval-token verification, and task/run
    API
- Landed GS approval/control demotions already changed the boundary:
  - `src/execution/trade_approval.py` removes the raw Telegram callback and
    `/tmp/gs_pending_approvals/*` approval loop, now fails closed when guarded
    approval context is missing or stale, and `89310b8` requires the approved
    `ticket_hash` so guarded approval binds to the reviewed payload
  - `src/execution/position_manager.py` now emits orchestrator approval handoff
    metadata on pending close proposals instead of carrying only
    `pending_manual_approval`
  - `dashboard/api/server.py` and `server.py` now demote the legacy
    `pending-orders` API and the dashboard frontend bridge assumptions that
    treated local pending-order files as terminal authority
- GS still owns major local dispatch and control authorities outside the landed
  demotions:
  - `scripts/agent_factory.py` local queue, worker loop, and `OpenClawStateDB`
  - `src/monitoring/telegram_command_handler.py` long-poll chat ingress plus a
    GS-local free-form chat adapter that now routes through Foundry
  - `src/bridges/openclaw_research_bridge.py` Telegram relay and reply polling
  - `scripts/ops/gs_control.py` and `src/risk/manual_veto_mcp.py` as local
    Tier-2 mutators that still need the same orchestrator demotion treatment

## Current GS Dispatch Entry Points

| Entry point | Current dispatch pattern | Why it blocks the target model | Target orchestrator posture |
| --- | --- | --- | --- |
| `src/inference/foundry_client.py` | Builds a GS envelope and posts to `ORCHESTRATOR_URL`, with direct Azure fallback on request failure. | The shared GS boundary and orchestrator `/v1/inference` route now exist, but not every runtime caller is fully migrated and the fallback path still preserves a provider escape hatch. | Keep this as the single GS inference boundary, verify `/v1/inference` in the deployed runtime, and retire fallback assumptions only after that path is stable. |
| `src/core/orchestrator_task_client.py` | Shared GS boundary for `POST /v1/tasks`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/history`, plus guarded payload helpers for `project`, `kind`, `target`, requester identity, and approval context. | The boundary is landed, but execution/control callers still need to converge on it instead of hand-rolling guarded payload contracts. | Treat this as the only GS task/run client surface and move remaining guarded callers behind it. |
| `scripts/ops/market_query.py` | Planner-style synchronous inference through `send_request(...)`, plus local reads of `control/manual_veto.json` and `control/kill_switch.json`. | The role mapping is good, but the operating-context read path still depends on GS-local control files. | Continue using the shared inference boundary; move control-state reads to an orchestrator-backed snapshot. |
| `scripts/ops/daily_thesis_generator.py` | Summarizer-style synchronous inference through `send_request(...)`, plus local control-file reads. | Same gap as `market_query.py`. | Continue using the shared inference boundary; read orchestrator-backed control state. |
| `src/monitoring/smart_inference_router.py` | Deprecated shim that classifies prompts and forwards into `foundry_client.send_request(...)`. | It preserves an old routing surface even though in-tree callers are gone. | Leave it as compatibility-only until external callers are migrated, then remove it. |
| `src/monitoring/telegram_command_handler.py` and `src/monitoring/telegram_bot_manager.py` | `getUpdates` long polling, Foundry-routed free-form chat, and Tier-2 command stubs that only print approval guidance. | GS still owns Telegram ingress and request shaping outside the shared task client boundary. | Reduce to a channel adapter that submits orchestrator tasks or orchestrator-backed inference requests. |
| `src/bridges/openclaw_research_bridge.py` | Sends `/gs_research` messages over Telegram and polls `getUpdates` for replies. | Research routing still goes through a Telegram relay instead of an orchestrator task/run contract. | Replace with a read-only advisory intent such as `research.market_brief` or a GS-specific research task kind. |
| `scripts/agent_factory.py` | Owns a local priority queue, worker threads, seeding cadence, `OpenClawStateDB`, and execution-adjacent task kinds such as `strategy_executor` and `crypto_executor`. | It is still a GS-local orchestrator with its own runtime state and dispatch policy. | Demote to helper code behind orchestrator-owned tasks, or retire it after equivalent orchestrator tasks exist. |
| `dashboard/api/server.py`, `server.py`, `scripts/ops/gs_control.py`, `src/risk/manual_veto_mcp.py` | The dashboard/server pending-orders bridge is demoted, but other control surfaces still include local file-oriented mutators and approval-guidance responses. | Pending-order files are no longer supposed to be terminal authority, yet some Tier-2 state changes still terminate inside GS-local surfaces. | Continue converting the remaining mutators into guarded `POST /v1/tasks` callers or read-only surfaces. |
| `src/execution/trade_approval.py` | Retained `request_approval()` now validates guarded orchestrator context up front, requires the approved `ticket_hash`, submits one scoped `gs.trade.execute_shadow` task when that context is valid, and no longer runs the raw Telegram or local pending-file approval loop. | The old `2bf35c5` integration breakage is cleared on `origin/main` by `89310b8`, but this still covers only one guarded execution entry point; other execution-capable flows still need to consume the same orchestrator task and approval contract consistently. | Keep the raw GS-owned approval transport removed, use this as the guarded trade-submission boundary, and extend the same contract across the remaining execution/control callers. |
| `src/execution/position_manager.py` | Emits orchestrator approval handoff metadata on pending close proposals instead of only a GS-local manual-approval marker. | The handoff metadata is now present, but downstream execution paths still need to consume the same guarded contract consistently. | Keep extending execution-capable flows to carry orchestrator task/target/approval metadata end to end. |
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

## Remaining Integration Points

### 1. Inference ingress is landed in source; runtime adoption still needs verification

GS's shared Foundry client points at `/v1/inference`, and the orchestrator
source plus tests now expose that route. The remaining work is to treat that
path as the stable runtime boundary and reduce assumptions that GS must fall
back to provider-local handling.

Consequence:

- the intended GS boundary exists end to end in source, but caller migration
  and live runtime verification still matter
- synchronous GS inference still depends on Azure fallback if the orchestrator
  endpoint is unavailable during rollout

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

### 3. GS-side task client boundary is landed, but adoption is incomplete

GS now has a shared task client in `src/core/orchestrator_task_client.py` for:

- `POST /v1/tasks`
- `GET /v1/runs/{id}`
- `GET /v1/runs/{id}/history`
- guarded helper payloads carrying:
  - `project`
  - `kind`
  - `target`
  - requester identity
  - `approval_jti`
  - `approval_reason`
  - `approval_exp`

Consequence:

- the shared boundary exists, but not every control/execution caller is using
  it yet
- guarded payload construction can still drift if modules keep hand-rolling the
  same fields instead of using the helper surface

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

### 5. Tier-2 mutation demotion is partial, not complete

Already demoted in the current GS mainline:

- `src/execution/trade_approval.py` no longer owns Telegram approval transport
  or local pending-approval files as authority, and `89310b8` now requires the
  approved `ticket_hash` so guarded submissions bind to the reviewed payload
- `dashboard/api/server.py` and `server.py` demote the `pending-orders` API and
  dashboard frontend bridge from terminal authority
- `src/execution/position_manager.py` now carries orchestrator approval handoff
  metadata instead of only a GS-local approval marker

Still requiring demotion or replacement:

- `config/execution_mode.yaml`
- `control/kill_switch.json`
- `control/manual_veto.json`
- `control/pending_approval_{strategy}.json`
- remaining execution-capable callers that still originate from GS-local flags
  or file-backed control state instead of one shared guarded task-client
  boundary
- local execution/control mutator surfaces that still write or read those files

Consequence:

- approval and control authority are less fragmented than before, but some
  mutation paths are still split between the orchestrator and GS-local files
- trade approval no longer terminates in raw Telegram or local pending files,
  and the specific guarded payload-binding blocker from `2bf35c5` is cleared on
  `origin/main` by `89310b8`, but that does not mean every execution/control
  path is already migrated to the same orchestrator contract
- remaining readers and writers still need an orchestrator-backed read model or
  compatibility snapshot

## Recommended Adoption Sequence

### Step 1. Stabilize the synchronous inference boundary that is already landed

Goal:

- treat the existing GS Foundry boundary plus orchestrator `/v1/inference`
  source route as the stable synchronous entrypoint before touching broader
  dispatch

Orchestrator touch points:

- `src/wrkflo_orchestrator/service.py`
- `src/wrkflo_orchestrator/foundry_client.py`
- `src/wrkflo_orchestrator/foundry.py`
- `tests/test_service.py`
- `docs/current-runtime-truth.md`

Work:

- keep `POST /v1/inference` healthy in source and deployment
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

- keep the existing `telegram_command_handler.py` free-form chat path on
  `send_request(...)` and avoid reintroducing provider-direct calls
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

Progress already landed in GS:

- `src/core/orchestrator_task_client.py` provides the shared GS task/run
  boundary and guarded payload helpers that should be used once these kinds
  exist on the orchestrator side

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

Progress already landed in GS:

- `trade_approval.py` removes the raw Telegram/local pending-file approval loop
  and now enforces guarded orchestrator context, including the payload-bound
  `ticket_hash` validation added in `89310b8`
- the `pending-orders` API/frontend bridge is already demoted in
  `dashboard/api/server.py` and `server.py`

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

Progress already landed in GS:

- `position_manager.py` now emits orchestrator approval handoff metadata for
  pending close proposals

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
- Do **not** treat `89310b8` as proof that the wider execution lane is fully
  migrated; it clears the `trade_approval.py` guarded payload-binding blocker,
  but other execution-capable callers still need to carry the same
  orchestrator task and approval contract end to end.
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
