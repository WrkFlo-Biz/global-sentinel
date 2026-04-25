# GS Guarded Task Kinds Plan

## Scope

This note defines the guarded `wrkflo-orchestrator` task kinds Global Sentinel
needs for the remaining Tier-2 actions that still terminate inside this repo.

It pairs with:

- `docs/migration-status.md`
- `docs/openclaw-demotion-plan.md`
- `docs/foundry-routing-adoption-plan.md`
- `/home/moses/projects/wrkflo-orchestrator/docs/openclaw-demotion.md`
- `/home/moses/projects/wrkflo-orchestrator/docs/current-runtime-truth.md`

This is a docs-only planning pass. No runtime code changed here.

## Current Contract Constraints

The current orchestrator approval model already fixes some design choices:

- guarded work is front-loaded: the bearer token is presented on the initial
  `POST /v1/tasks`
- approval claims currently include only:
  `jti`, `kind`, `target`, `tier`, `nbf`, `exp`, `issued_by`, `reason`
- `validate_approval_claims(...)` verifies exact `kind` and exact `target`
- the service rejects revoked tokens and consumes each token once
- the TTL hard cap is 15 minutes
- there is no live suspended-run `/approve` endpoint to map GS pending files or
  Telegram callback approvals onto

Implications for GS:

1. Do not add `gs.trade.approve` or `gs.trade.reject` task kinds.
   Those names preserve the old late-binding approval model that the current
   orchestrator runtime does not implement.
2. Do not mint guarded tokens against bare `target=global-sentinel`.
   The token contract is too coarse for that. A token must describe the exact
   action scope, not the entire project.
3. Multi-order execution lanes must decompose into per-ticket guarded submits.
   A single package-wide token would be too broad because the current verifier
   does not bind approval to a payload digest.

## Proposed Guarded Kinds

| Kind | Proposed target format | Current GS surfaces that collapse into it | Notes |
| --- | --- | --- | --- |
| `gs.control.execution_mode.set` | `global-sentinel/control/execution-mode/<strategy>/<mode>` | `dashboard/api/server.py` `POST /api/execution-mode`; `server.py` `POST /api/execution-mode`; `scripts/ops/gs_control.py` `mode`; Telegram `/gs_mode` stub in `src/monitoring/telegram_command_handler.py` | One token per exact strategy and target mode. Example target: `global-sentinel/control/execution-mode/day_trade/manual`. |
| `gs.control.kill_switch.set` | `global-sentinel/control/kill-switch/<on|off>` | `dashboard/api/server.py` `POST /api/control/kill-switch`; `dashboard/api/server.py` `POST /api/v6/kill-switch`; `dashboard/api/server.py` `POST /api/v6/kill-switch/deactivate`; mirrored `server.py` endpoints; `scripts/ops/gs_control.py` `kill` and `unkill`; `src/risk/manual_veto_mcp.py` `set_kill_switch`; Telegram `/gs_kill` stub | `clear_all_flags` should issue this kind plus `gs.control.manual_veto.set`; do not introduce a broad combined clear kind first. |
| `gs.control.manual_veto.set` | `global-sentinel/control/manual-veto/<on|off>` | `dashboard/api/server.py` `POST /api/control/veto`; mirrored `server.py` endpoint; `scripts/ops/gs_control.py` `veto` and `unveto`; `src/risk/manual_veto_mcp.py` `set_manual_veto`; Telegram `/gs_veto` stub | Same decomposition rule as kill switch. Example target: `global-sentinel/control/manual-veto/on`. |
| `gs.trade.execute_shadow` | `global-sentinel/trade-ticket/<ticket_id>` | `src/execution/trade_approval.py`; `src/execution/shadow_order_router.py`; `src/execution/multi_broker_router.py` `route_and_execute(...)` and `route_conditional_order(...)`; `scripts/ops/conditional_order_engine.py`; `scripts/agent_factory.py` `strategy_executor` and `crypto_executor`; Telegram `/gs_approve` and `/gs_reject` stubs | One guarded token per normalized ticket. The worker must validate the ticket payload against the ticket id or digest because the base approval claims do not bind the full payload. |

## Why The Targets Must Be This Specific

The current approval verifier checks only `kind` and `target`. That means the
target string itself has to carry enough scope to stop accidental or malicious
payload substitution.

Examples:

- `global-sentinel/control/kill-switch/on` is safe enough for phase 1 because a
  token issued for `on` cannot also be reused for `off`.
- `global-sentinel/control/execution-mode/day_trade/manual` is safer than a
  generic `global-sentinel/control/execution-mode/day_trade` because the token
  is bound to the exact transition.
- `global-sentinel/trade-ticket/<ticket_id>` is the narrowest useful scope for
  execution, but only if GS and the worker can prove what that `ticket_id`
  means. A raw order payload alone is not enough.

Required worker-side rule:

- every guarded worker must reject payload values that do not match the target
  semantics

Examples:

- `gs.control.kill_switch.set` with target suffix `/on` must reject
  `active=false`
- `gs.control.execution_mode.set` with target
  `.../day_trade/manual` must reject `strategy=medium_long` or `mode=auto`
- `gs.trade.execute_shadow` must reject a ticket whose resolved digest, symbol,
  side, size, or account no longer matches the stored ticket referenced by
  `<ticket_id>`

## Companion Non-Guarded Prerequisite

`gs.trade.execute_shadow` is the guarded execution kind, but GS still needs a
non-guarded way to build a concrete ticket before a human can mint a token for
that exact target.

Recommended companion slice:

- `gs.trade.prepare_ticket`
- target shape:
  `global-sentinel/trade-ticket/<ticket_id>/draft`
- tier posture: Tier-1 advisory or another non-guarded path, not a new
  late-binding approval surface

`gs.trade.prepare_ticket` should return:

- `ticket_id`
- `ticket_hash`
- normalized order fields
- a human-readable summary
- provenance such as `source_surface`, `candidate_id`, `package_id`, or
  `router_run_id`

Then the operator mints:

```bash
wrkflo-orchestrator approve \
  --kind gs.trade.execute_shadow \
  --target global-sentinel/trade-ticket/<ticket_id> \
  --reason "approve GS shadow ticket"
```

This keeps the current orchestrator model intact:

1. prepare the ticket out of band
2. mint a token for the exact execute target
3. submit the guarded task once through `POST /v1/tasks`

## Required Request And Identity Fields

### Common guarded envelope

These fields should be present on every GS guarded task submission.

| Field | Required now | Why it is needed |
| --- | --- | --- |
| `project` | yes | Must be `global-sentinel` so task history and worker routing can group GS runs correctly. |
| `kind` | yes | Required for both orchestrator routing and approval matching. |
| `target` | yes | Exact approval scope. Must never be the bare project name for Tier-2 work. |
| `requester` | yes | Current orchestrator code already propagates this string into worker execution and audit. |
| `requester_kind` | yes | Distinguishes `telegram`, `dashboard`, `cli`, `mcp`, or `scheduler`. |
| `requester_id` | yes | Stable origin identity such as chat id, API subject, user@host, or session id. |
| `requester_channel` | yes | Concrete surface name such as `mo2darkbot`, `dashboard`, `gs_control`, or `manual_veto_mcp`. |
| `reason` | yes | Operator intent or trigger rationale. Must be explicit, not inferred from the token alone. |
| `requested_at` | yes | ISO-8601 timestamp for GS-local audit correlation. |
| `run_id` | optional today, recommended | The current service can generate this if absent, but callers should be allowed to supply it for cross-system correlation. |
| `idempotency_key` | planned, not live today | Needed for retriable dashboard/CLI/MCP submits. Current orchestrator source does not enforce it yet, so this is an orchestrator follow-up. |
| `approval_jti` | no, orchestrator-stamped | GS needs the consumed token id for fail-closed execution and audit joins. |
| `approval_issued_by` | no, orchestrator-stamped | The token issuer is the durable human approver; it is not the same as the caller surface. |
| `approval_reason` | no, orchestrator-stamped | Needed when GS mirrors or reports the approval context locally. |
| `approval_exp` | no, orchestrator-stamped | Lets GS fail closed if guarded context is stale before final submission. |

### Kind-specific fields

| Kind | Required payload fields beyond the common envelope |
| --- | --- |
| `gs.control.execution_mode.set` | `strategy`, `mode` |
| `gs.control.kill_switch.set` | `active` |
| `gs.control.manual_veto.set` | `active` |
| `gs.trade.execute_shadow` | `ticket_id`, `ticket_hash`, `strategy`, `account`, `symbol`, `side`, one of `qty` or `notional`, `asset_class`, `order_type`, `time_in_force`, `source_surface`; include `limit_price` for limit orders and provenance like `candidate_id`, `package_id`, `client_order_id`, or `router_run_id` when available |

### Identity capture by current surface

| Current surface | Required identity capture |
| --- | --- |
| Telegram command stubs | `requester_kind=telegram`, `requester_id=<chat_id>`, `requester_channel=<bot_username>`, message id or command text in the payload provenance |
| Dashboard API | `requester_kind=dashboard`, `requester_id=<authenticated subject or key id>`, remote address or session id if available |
| `gs_control.py` | `requester_kind=cli`, `requester_id=<user@host>`, `requester_channel=gs_control` |
| `manual_veto_mcp.py` | `requester_kind=mcp`, `requester_id=<session or caller id>`, `requester_channel=manual_veto_mcp` |
| Scheduled or autonomous GS lanes | `requester_kind=scheduler`, `requester_id=<service/unit name>`, plus the upstream run or package provenance |

Important distinction:

- `requester_*` fields describe where the task was submitted from
- `approval_*` fields describe who actually approved the guarded action

Both are needed.

## Current GS Surface Mapping

### Control-plane mutation

These should all become thin clients of the same guarded-task path:

- `dashboard/api/server.py`
- `server.py`
- `scripts/ops/gs_control.py`
- `src/risk/manual_veto_mcp.py`

Target mapping:

- execution mode writers -> `gs.control.execution_mode.set`
- kill-switch writers -> `gs.control.kill_switch.set`
- manual-veto writers -> `gs.control.manual_veto.set`
- `clear_all_flags` -> two separate guarded calls, not a new broad kind

### Legacy approval transport

`src/execution/trade_approval.py` should not survive as its own approval
surface. Its Telegram callback polling and local pending files are a legacy
transport, not a future orchestrator kind.

Replacement:

- prepare a concrete ticket first
- mint a token for `gs.trade.execute_shadow`
- submit the guarded task once

That also means `POST /api/telegram/approve` stays dead. There is no reason to
reintroduce it under a new late-binding kind name.

### Execution-capable automation

These surfaces must stop submitting or flattening orders from local GS state
alone:

- `src/execution/shadow_order_router.py`
- `src/execution/multi_broker_router.py`
- `scripts/ops/conditional_order_engine.py`
- `scripts/agent_factory.py` `strategy_executor`
- `scripts/agent_factory.py` `crypto_executor`

They all map to the same guarded execution boundary:

- `gs.trade.execute_shadow`

Design rule:

- no package-wide or strategy-wide guarded token in phase 1
- each candidate or order must normalize into its own `ticket_id`
- each guarded submit executes one exact ticket target

That keeps the approval scope narrow enough for the current token contract.

## GS Code Changes Needed

### 1. Add one shared GS task client boundary

GS currently has a shared inference client in `src/inference/foundry_client.py`
but no equivalent shared task client.

Recommended new boundary:

- `src/core/orchestrator_task_client.py` or similar

It should own:

- `POST /v1/tasks`
- `GET /v1/runs/{id}`
- `GET /v1/runs/{id}/history`
- bearer token plumbing
- task envelope normalization

Every current write surface should call that client instead of mutating local
files directly.

### 2. Replace direct file writers with guarded submits

The direct writers in `dashboard/api/server.py`, `server.py`,
`scripts/ops/gs_control.py`, and `src/risk/manual_veto_mcp.py` should stop
being the terminal authority over:

- `config/execution_mode.yaml`
- `control/kill_switch.json`
- `control/manual_veto.json`

Short-term compatibility is still allowed, but only as a cache or materialized
view sourced from orchestrator-validated runs, not as the primary approval
authority.

### 3. Introduce normalized ticket creation

GS needs a common ticket builder used by:

- `ShadowOrderRouter`
- `conditional_order_engine`
- `strategy_executor`
- `crypto_executor`

That builder should emit:

- `ticket_id`
- `ticket_hash`
- normalized order fields
- provenance fields

Without that, `gs.trade.execute_shadow` has no stable target to bind approval
to.

### 4. Remove Telegram approval transport from execution

`src/execution/trade_approval.py` should stop:

- sending Telegram inline-button approvals
- polling `getUpdates`
- writing `/tmp/gs_pending_approvals`
- acting as the approval source of truth

Its replacement job is much smaller:

- validate orchestrator-stamped guarded context
- fail closed if `approval_jti`, `approval_issued_by`, `approval_reason`, or
  `approval_exp` are missing or stale

### 5. Enforce guarded context at execution boundaries

`ShadowOrderRouter` and `multi_broker_router` should reject execution when the
guarded context is missing, mismatched, or stale.

At minimum they should validate:

- `kind == gs.trade.execute_shadow`
- `target == global-sentinel/trade-ticket/<ticket_id>`
- `ticket_id`
- `ticket_hash`
- `approval_jti`
- `approval_issued_by`
- `approval_reason`

### 6. Decompose automation lanes into per-ticket submits

`conditional_order_engine`, `strategy_executor`, and `crypto_executor` should
not receive one broad approval token for an entire package or cycle.

Instead:

- each proposed order becomes a normalized ticket
- each ticket is reviewed and approved independently
- each execution-capable submit is one `gs.trade.execute_shadow` run

## Orchestrator Code Changes Needed

### 1. Register GS guarded kinds in the service

`/home/moses/projects/wrkflo-orchestrator/src/wrkflo_orchestrator/service.py`
needs to recognize the GS kinds in `TIER_MAP` and route them to a real worker
implementation.

Minimum additions:

- `gs.control.execution_mode.set`
- `gs.control.kill_switch.set`
- `gs.control.manual_veto.set`
- `gs.trade.execute_shadow`

### 2. Add a GS worker module

Recommended new module:

- `/home/moses/projects/wrkflo-orchestrator/src/wrkflo_orchestrator/workers/global_sentinel_worker.py`

It should:

- validate each GS payload shape
- validate payload-to-target consistency
- attach or forward the verified approval context
- execute the GS-side action through one bounded interface

`workers/registry.py` will then discover it like the existing worker modules.

### 3. Stamp verified approval metadata into the task payload or run state

Today `_authorize_task(...)` verifies and consumes the token, but the verified
claims are not surfaced back into the task payload that GS workers would
consume.

That is a blocker for fail-closed GS execution.

After successful auth, the orchestrator should stamp:

- `approval_jti`
- `approval_issued_by`
- `approval_reason`
- `approval_exp`

into the payload or an equivalent run-visible structure.

### 4. Add caller idempotency support

The control surfaces that will proxy into `/v1/tasks` are HTTP, CLI, and MCP
callers that may retry on timeout.

Recommended addition:

- accept an explicit `idempotency_key`
- dedupe repeated guarded submissions by `kind + target + idempotency_key`

If that is deferred, callers should at least be allowed to provide a stable
`run_id`, but explicit idempotency support is cleaner.

### 5. Extend tests and runtime docs

Minimum orchestrator follow-ups:

- `tests/test_service.py` coverage for the GS kinds
- target/payload mismatch tests
- replay/revocation tests for the new kinds
- worker registration coverage
- runtime docs updates in `docs/current-runtime-truth.md` and
  `docs/openclaw-demotion.md`

Optional future hardening:

- extend approval claims with a payload digest if broad target reuse becomes a
  problem later

That is not required for phase 1 if the targets remain narrow and the worker
validates the payload against the target.

## Kinds We Should Not Add

Do not add these in the first guarded slice:

- `gs.trade.approve`
- `gs.trade.reject`
- `gs.control.set`
- `gs.control.flags.clear_all`
- package-wide execution targets such as
  `global-sentinel/strategy-executor/<cycle_id>`

Those all make the approval scope broader than the current token contract can
defend safely.

## Recommended Rollout Order

1. Land `gs.control.kill_switch.set` and `gs.control.execution_mode.set` first.
   These are the easiest guarded slices to validate and audit.
2. Stamp verified approval metadata into orchestrator task payloads.
   GS execution code cannot fail closed without that context.
3. Add `gs.trade.prepare_ticket` as the non-guarded ticket-draft path.
4. Only then land `gs.trade.execute_shadow`.
5. Move `conditional_order_engine`, `strategy_executor`, and `crypto_executor`
   onto per-ticket guarded submits after the single-ticket path is proven.

## Minimal Success Condition

This lane is complete when all of the following are true:

- GS has explicit guarded kinds for the remaining Tier-2 control actions
- the execution path binds approval to a concrete per-ticket target instead of
  Telegram callbacks or local files
- the orchestrator passes verified approval metadata into the GS worker path
- no caller mints a Tier-2 token against bare `target=global-sentinel`
- `/api/telegram/approve` remains retired rather than being reintroduced as a
  late-binding approval surface
