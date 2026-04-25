# GS Migration Status — 2026-04-25 UTC

## Scope

This note is the current Global Sentinel status for the GS migration lane only:

- document current OpenClaw demotion status
- identify approval and control flows still using raw Telegram or GS-local
  files
- map the concrete GS-side changes needed to adopt the
  `wrkflo-orchestrator` approval-token flow

Inputs for this pass:

- `/tmp/claude-sync-to-orchestrator.md`
- current GS tree under `/home/moses/projects/global-sentinel`
- existing GS docs:
  `docs/openclaw-demotion.md`,
  `docs/openclaw-demotion-plan.md`,
  `docs/permission-tiers.md`,
  `docs/architecture-delta-gs-view.md`,
  `docs/foundry-routing-adoption-plan.md`
- orchestrator approval-token implementation and runtime docs under
  `/home/moses/projects/wrkflo-orchestrator`

## Status Summary

- The handoff's Phase 0-4 work is done and is not the blocker for this lane.
- GS migration is partially complete, not finished.
- The biggest completed demotion step is that Tier-2 Telegram bot commands are
  now stubs instead of direct mutators.
- The biggest remaining blockers are:
  - GS still owns Telegram chat ingress and research relay
  - GS still exposes Tier-2 local-file mutators through API, CLI, and MCP
  - OpenClaw runtime state still lives in `src/core/openclaw_state_db.py`
  - execution-adjacent OpenClaw seeding still exists inside
    `scripts/agent_factory.py`
- Two deeper Phase 5 companion docs now exist and should be read with this
  status note:
  - `docs/openclaw-demotion-plan.md` for the `OpenClawStateDB` retirement and
    runtime-state demotion sequence
  - `docs/foundry-routing-adoption-plan.md` for the Foundry/orchestrator
    routing adoption sequence and control-plane routing blockers
- The target approval model is the orchestrator's current beta:
  front-loaded guarded submission with `Authorization: Bearer <token>` on the
  initial `POST /v1/tasks`. There is no current suspended-run `/approve`
  endpoint to map GS pending-approval files onto.

## 1. OpenClaw Demotion Status

### Already demoted or bounded

- `src/monitoring/telegram_command_handler.py:429-447,578-618` no longer
  mutates GS state for `/gs_mode`, `/gs_kill`, `/gs_veto`, `/gs_approve`,
  `/gs_reject`, or `/gs_refresh`; those commands only return
  `ORCHESTRATOR_APPROVAL_MESSAGE`.
- The target GS-orchestrator boundary is already stated correctly in
  `docs/architecture-delta-gs-view.md`: GS computes risk/policy facts, while
  the orchestrator owns approval transport and routed execution.
- The deeper OpenClaw coupling inventory is already captured in
  `docs/openclaw-demotion.md`; this file narrows that audit to the current
  migration lane.

### Still not demoted

- **GS still owns Telegram ingress and chat handling.**
  `src/monitoring/telegram_bot_manager.py:33-116` still starts long-polling
  handlers, and `src/monitoring/telegram_command_handler.py:129-140,258-333`
  still owns `getUpdates`, chat dispatch, and direct Anthropic chat replies.
- **GS still owns the OpenClaw research relay.**
  `src/bridges/openclaw_research_bridge.py:1-21,95-214` still sends
  `/gs_research` messages over Telegram and polls `getUpdates` for replies.
- **OpenClaw runtime state still lives inside GS.**
  `scripts/agent_factory.py:1011-1090` instantiates `OpenClawBot` with
  `OpenClawStateDB`, while `src/core/openclaw_state_db.py:39-220` still owns
  `state.db`, `task_history`, `worker_health`, and `audit_log`.
- **OpenClaw proposal/research seeding is still execution-adjacent.**
  `scripts/agent_factory.py:372-376` still emits
  `requires_human_approval: false` and `execution_enabled: true`, and
  `scripts/agent_factory.py:1299-1319` can still enqueue
  `strategy_executor`, `crypto_executor`, and tuning work from the GS-owned
  loop.
- **Tier-2 state authority is still GS-local.**
  API, CLI, and MCP paths still write `execution_mode.yaml`,
  `kill_switch.json`, `manual_veto.json`, and legacy approval files directly.

### Current demotion read

OpenClaw is only partially demoted. The bot UX layer has stopped mutating GS
state directly, but the actual control-plane authority has not moved out of GS
yet. Telegram transport, Tier-2 control mutation, and OpenClaw runtime state
still terminate inside this repo.

Use `docs/openclaw-demotion-plan.md` for the file-level retirement order of
`OpenClawStateDB` and adjacent runtime state, and
`docs/foundry-routing-adoption-plan.md` for the control-plane ingress and
Foundry/orchestrator routing prerequisites that have to land before the final
demotion steps can stick.

## 2. Approval And Control Flows Still Using Raw Telegram Or Local Files

Tier-2 Telegram bot commands are **not** in this table because they are already
stubs. The remaining problem surfaces are the still-live transport and state
authorities behind those stubs.

| Flow | Current code | Legacy pattern still present | Current status |
| --- | --- | --- | --- |
| Direct trade approval transport | `src/execution/trade_approval.py:28-34,245-385,413-623` | Sends Telegram inline-button approvals, polls `getUpdates` callback queries, writes `/tmp/gs_pending_approvals/*.pending`, `/tmp/gs_pending_approvals/*.decision`, and `/tmp/gs_callback_offset.json`, then mirrors audit into `logs/trade_approvals.jsonl` and `OpenClawStateDB`. | This is the only raw Telegram approval transport left in active code. It is fail-closed now, but it is still a GS-owned Telegram plus local-file approval loop. No non-test in-tree caller was found in this pass, so it is currently a retained legacy module rather than a clearly wired runtime path. |
| Legacy approval-file API | `dashboard/api/server.py:2970-3004`; `server.py:629-660` | `POST /api/telegram/approve` writes `control/pending_approval_{strategy}.json`; `GET /api/pending-orders` reads `control/pending_orders_{strategy}.json`. | This is still a live local-file approval surface even though the current Telegram command handler no longer calls it. No in-tree consumer of `pending_approval_{strategy}.json` was found in this pass, so the surface appears stale but still reachable. |
| Execution-mode mutator | `dashboard/api/server.py:2946-2967`; `server.py:605-626` | Direct rewrite of `config/execution_mode.yaml`. | Tier-2 control mutation still terminates in a GS-local file instead of an orchestrator verdict. |
| Kill/veto mutators | `dashboard/api/server.py:3993-4019,4968-4987`; `server.py:833-859`; `src/risk/manual_veto_mcp.py:112-142,209-221` | Direct rewrites of `control/kill_switch.json` and `control/manual_veto.json` via API, v6 API, or MCP tool calls. | These are local-file control lanes outside orchestrator approval. |
| Localhost control CLI | `scripts/ops/gs_control.py:147-169` | Direct POSTs to `/api/control/kill-switch`, `/api/control/veto`, and `/api/execution-mode`. | This keeps a mutable OpenClaw/SSH/Termius-to-GS control path alive even after bot-command demotion. |

### Important nuance

- `src/execution/trade_approval.py` is no longer fail-open on missing Telegram
  config, send failures, or timeouts; those cases block the trade. The
  remaining problem is **legacy authority and transport**, not the old
  fail-open behavior.
- `src/monitoring/telegram_command_handler.py` no longer calls
  `/api/telegram/approve` or the control mutator endpoints. The bot UX is ahead
  of the state authority behind it.
- `server.py` and `dashboard/api/server.py` both carry the same mutator
  surfaces, which increases drift risk during migration.

### Readers that still assume local files are authoritative

Even after the write paths move, several GS components still read these local
control files as source of truth. Representative readers found in this pass:

- `src/monitoring/crisis_monitor.py:191-210`
- `scripts/agent_factory.py:142-146`
- `src/execution/politician_alpha_executor.py:95-96,279-292`
- `scripts/ops/market_query.py:94-95`
- `scripts/ops/daily_thesis_generator.py:73-89`

That means the migration is not just "replace writers"; GS also needs a new
authoritative read path or a compatibility cache sourced from orchestrator
verdicts.

## 3. What Must Change To Adopt The Orchestrator Approval-Token Flow

### Token contract that already exists on the orchestrator side

The orchestrator already has a concrete guarded-task contract:

- approval tokens are HMAC-SHA256 bearer tokens minted by
  `wrkflo-orchestrator approve --kind <kind> --target <target> --reason <reason>`
- token claims include:
  `jti`, `kind`, `target`, `tier`, `nbf`, `exp`, `issued_by`, `reason`
- `tier` must equal `2`
- the TTL hard cap is 15 minutes
- guarded tasks require `Authorization: Bearer <token>` on the initial
  `POST /v1/tasks`
- the orchestrator verifies exact `kind` and exact `target`, rejects revoked
  tokens, and consumes each token once
- approval use is recorded in orchestrator state through
  `approval_tokens_used` and `audit_events`

Relevant orchestrator references used in this pass:

- `/home/moses/projects/wrkflo-orchestrator/src/wrkflo_orchestrator/approval.py`
- `/home/moses/projects/wrkflo-orchestrator/src/wrkflo_orchestrator/service.py`
- `/home/moses/projects/wrkflo-orchestrator/docs/openclaw-demotion.md`

### Consequence for GS

GS cannot keep a local "pending approval, wait for reply later" model and still
match the current orchestrator runtime. The current beta is front-loaded:

1. the caller obtains a token out of band
2. the guarded task is submitted once through `POST /v1/tasks`
3. the orchestrator records token use and returns a `run_id`
4. subsequent calls only read run state or run history

That makes these GS patterns incompatible with the target model:

- Telegram callback polling
- `/tmp/gs_pending_approvals`
- `control/pending_approval_{strategy}.json`
- direct `kill_switch.json` / `manual_veto.json` writes as the terminal
  authority
- any GS flow that waits for a later `/approve` mutation instead of requiring
  approval context at submission time

### Implementation-track summary

Phase 5 now has two companion implementation docs that should be read with this
status note:

- `docs/openclaw-demotion-plan.md`
- `docs/foundry-routing-adoption-plan.md`

Their combined sequence is:

1. close the Foundry routing ingress gap by making the orchestrator serve the
   GS synchronous inference contract at `/v1/inference`
2. finish moving active GS runtime callers onto the shared routing boundary
3. register explicit GS task kind definitions and bind them to
   `target=global-sentinel`
4. move OpenClaw runtime state and dispatch ownership out of
   `OpenClawStateDB` and `scripts/agent_factory.py`
5. collapse Tier-2 local mutators and execution-adjacent approvals into
   guarded orchestrator submission

The routing blockers are also shared across both plans:

- live `/v1/inference` is still missing
- no GS-specific task kind registry was found in orchestrator
- GS still lacks a shared task client boundary for `/v1/tasks` and `/v1/runs/*`
- local control files and `OpenClawStateDB` are still treated as authoritative

The key approval-token dependency is the same in both plans: guarded
replacement flows only work once GS actions can bind to an explicit task kind
plus `target=global-sentinel`. Without that contract, OpenClaw demotion cannot
finish and Foundry/orchestrator routing cannot absorb Tier-2 control or
execution-capable GS paths safely. In practice, that means the routing plan
has to establish the task and ingress contract before the demotion plan can
retire the last GS-local approval and runtime-state authorities.

### Concrete GS-side migration map

1. **Replace legacy approval transport with guarded task submission.**
   - `src/execution/trade_approval.py` should stop sending Telegram messages,
     polling `getUpdates`, and writing `/tmp/gs_pending_approvals`.
   - The execution boundary should instead require an
     orchestrator-approved context on entry:
     `kind`, `target`, `run_id`, `approval_jti`, `issued_by`, `reason`,
     `exp` (or an equivalent orchestrator verdict object).
   - If that context is missing, expired, replayed, revoked, or mismatched,
     GS must fail closed before any broker submission.

2. **Define real guarded task kinds for GS Tier-2 actions.**
   - At minimum GS needs orchestrator task kinds for:
     - execution mode changes
     - kill switch changes
     - manual veto changes
     - guarded trade execution or trade ticket preparation
   - Whatever names are chosen, they must be bound to `target=global-sentinel`
     so the token verifier's `kind` and `target` checks are meaningful.
   - Today this is still a gap: no GS-specific guarded task kinds were found in
     the orchestrator source during this pass.

3. **Retire GS-local approval files as the source of truth.**
   - Remove or demote:
     - `control/pending_approval_{strategy}.json`
     - `control/pending_orders_{strategy}.json`
     - direct write authority over `execution_mode.yaml`
     - direct write authority over `kill_switch.json`
     - direct write authority over `manual_veto.json`
   - Short term, GS can keep a compatibility cache if existing readers still
     need file-backed inputs.
   - Long term, GS readers should consume orchestrator-derived state or a
     GS-local snapshot built from orchestrator verdicts.

4. **Collapse API, CLI, and MCP Tier-2 surfaces into one path.**
   - `dashboard/api/server.py`, `server.py`, `scripts/ops/gs_control.py`, and
     `src/risk/manual_veto_mcp.py` should stop acting as terminal mutators.
   - Each should become one of:
     - read-only
     - removed
     - a thin orchestrator proxy that requires a bearer token and only records
       the returned `run_id`

5. **Move OpenClaw runtime state out of `OpenClawStateDB`.**
   - `scripts/agent_factory.py` should stop owning worker/task state in
     `state.db`.
   - Worker/task runtime state belongs in orchestrator state once OpenClaw
     becomes a channel adapter instead of a GS-owned runtime.
   - If GS still wants a local audit trail, it should log orchestrator
     `run_id` and approval `jti` as a mirror, not keep `OpenClawStateDB` as the
     approval authority.

6. **Finish chat demotion after the approval path is real.**
   - Once guarded task kinds exist, the current
     `ORCHESTRATOR_APPROVAL_MESSAGE` can be replaced with actual orchestrator
     task submission from the channel side, or the GS bot can be reduced to
     read-only status commands.
   - The long-term target is that Telegram/OpenClaw prompts and replies live
     outside GS, with GS only handling domain work after orchestrator routing.

### Minimal target sequence

For a guarded GS action, the end state should look like this:

1. operator or channel adapter mints a token:
   `wrkflo-orchestrator approve --kind <gs-kind> --target global-sentinel --reason "..."`
2. the caller submits one guarded `POST /v1/tasks` with:
   - `Authorization: Bearer <token>`
   - `kind`
   - `target: "global-sentinel"`
   - `project: "global-sentinel"`
   - requester identity
   - idempotency key
   - GS decision context needed for policy or execution
3. orchestrator validates and consumes the token, records audit state, and
   returns `run_id`
4. GS acts only on the orchestrator-approved request and logs the returned
   `run_id` / `approval_jti`
5. status and follow-up are read back through `GET /v1/runs/{id}` or
   `GET /v1/runs/{id}/history`

## Verification Commands

```bash
cd /home/moses/projects/global-sentinel

sed -n '1,260p' docs/migration-status.md

rg -n "ORCHESTRATOR_APPROVAL_MESSAGE|Anthropic\\(|getUpdates|OpenClawStateDB|execution_enabled|requires_human_approval" \
  src/monitoring/telegram_command_handler.py \
  src/bridges/openclaw_research_bridge.py \
  src/execution/trade_approval.py \
  scripts/agent_factory.py \
  src/core/openclaw_state_db.py

rg -n "/api/execution-mode|/api/telegram/approve|/api/control/kill-switch|/api/control/veto|/api/v6/kill-switch" \
  dashboard/api/server.py server.py scripts/ops/gs_control.py

rg -n "pending_approval_|pending_orders_|gs_pending_approvals|gs_callback_offset|set_manual_veto|set_kill_switch|clear_all_flags" \
  src scripts dashboard/api/server.py server.py
```

## Residual Risks

- The orchestrator approval-token contract exists, but no GS-specific guarded
  task kinds were found in the orchestrator source during this pass. That is
  the main implementation gap after the docs work.
- `server.py` and `dashboard/api/server.py` duplicate the same Tier-2 mutator
  surfaces, so migration drift is possible unless both are removed or unified.
- `trade_approval.py` appears retained rather than wired from active runtime
  code today, which lowers immediate runtime risk but raises future regression
  risk if another path re-imports it before the migration is finished.
- Many GS readers still assume local control files are authoritative; replacing
  writers without replacing readers will leave the system split-brained.
