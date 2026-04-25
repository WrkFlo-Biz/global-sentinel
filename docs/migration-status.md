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
- landed GS migration commits relevant to this status note on `origin/main`:
  - `a0937aa` `feat: add guarded gs task client helpers`
  - `dcf00b1` `fix: add orchestrator approval handoff to position manager`
  - `2bf35c5` `fix: route trade approval through orchestrator mediation`
  - `072ad10` `fix: demote frontend approval bridge ux`
  - `89310b8` `fix: require approved ticket hash for guarded trades`
  - `7eb3b76` `docs: correct Foundry routing blocker status`
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
- A meaningful tranche of the GS approval demotion work is now landed on
  `origin/main`:
  - GS now has a shared task-client boundary in
    `src/core/orchestrator_task_client.py` for `/v1/tasks` and `/v1/runs/*`
  - `src/execution/trade_approval.py` is now an orchestrator-mediated,
    fail-closed guarded execution boundary instead of a Telegram callback loop,
    and `89310b8` now requires the approved `ticket_hash` so guarded approval
    binds to the reviewed payload
  - `src/execution/position_manager.py` now emits per-ticket orchestrator
    approval handoff metadata for guarded close proposals
  - `POST /api/telegram/approve`, `GET /api/pending-orders`, and the dashboard
    frontend approval bridge assumptions are demoted
  - `7eb3b76` updates the companion Foundry routing doc so the resolved
    `2bf35c5` integration breakage is no longer described as the live blocker
- The biggest remaining blockers are:
  - GS still owns Telegram chat ingress and research relay
  - OpenClaw runtime state still lives in `src/core/openclaw_state_db.py`
  - execution-adjacent OpenClaw seeding still exists inside
    `scripts/agent_factory.py`
  - the orchestrator still lacks a confirmed GS-specific kind registry and
    end-to-end worker routing/execution path for all guarded task payloads GS
    can now emit
  - many GS readers still treat local files as the source of truth even after
    the mutator demotions
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
- `src/core/orchestrator_task_client.py:1-220` now exists as a shared GS-side
  client boundary for orchestrator task endpoints. It provides
  `submit_task()`, `get_run()`, `get_run_history()`, and guarded payload
  helpers so execution/control surfaces do not need to hand-roll
  `project`/`target`/approval metadata.
- `src/execution/trade_approval.py:1-214,466-664` is no longer a Telegram or
  local-pending-file approval loop. It now validates orchestrator-stamped
  guarded context (`approval_jti`, `approval_reason`, `approval_exp`,
  scoped `target`, `ticket_id`, and required `ticket_hash`), submits one
  scoped `gs.trade.execute_shadow` task through the shared task client, and
  keeps only a local JSONL audit mirror plus compatibility stubs for the
  legacy file bridge.
- `src/execution/position_manager.py:430-496` now emits explicit orchestrator
  approval handoff metadata for guarded close proposals:
  `approval_required`, `ticket_id`, `ticket_hash`, `kind`, `target`, and
  `orchestrator_command`. It no longer only sets a bare
  `pending_manual_approval`-style flag.
- `dashboard/api/server.py:3025-3041`; `server.py:684-700`;
  `dashboard/frontend/src/lib/api.ts:263-290`; and
  `dashboard/frontend/src/components/ExecutionModePanel.tsx:42-70` now demote
  the old approval-file bridge. `POST /api/telegram/approve` returns
  `410 legacy_approval_file_bridge_disabled`, `GET /api/pending-orders`
  returns an `approval_required` payload, and the frontend explicitly states
  those routes are no longer the source of truth.
- Dashboard/root API control mutators, the localhost `gs_control.py` surface,
  and `src/risk/manual_veto_mcp.py` are also in demoted-state guidance mode:
  they return scoped orchestrator approval commands instead of writing control
  files directly.
- The target GS-orchestrator boundary is already stated correctly in
  `docs/architecture-delta-gs-view.md`: GS computes risk/policy facts, while
  the orchestrator owns approval transport and routed execution.
- `docs/foundry-routing-adoption-plan.md` now reflects the blocker-cleared
  state after `89310b8`: the old `2bf35c5` trade-approval integration breakage
  is historical context, while the live routing gap is the broader GS
  task-kind, worker-routing, and runtime-adoption work.
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
- **GS now has a shared read boundary, but the last contract cleanup is still above it.**
  Most first-party kill-switch and manual-veto readers now converge on
  `src/core/control_state_snapshot.py`, and the root/dashboard
  `GET /api/controls` plus websocket `controls` wrappers now normalize their
  booleans through that helper. In the current GS tree, the active
  dashboard/frontend consumer lane also repoints the main dashboard poll path
  and project-facing config/prompt surfaces to `GET /api/control/status`.
  The remaining drift is now the dual public contract: backend compatibility
  wrappers are still published, and the frontend API layer still keeps
  wrapper-shape fallback while that cleanup finishes.

### Current demotion read

OpenClaw is only partially demoted. The bot UX layer has stopped mutating GS
state directly, and the trade-approval plus pending-order bridges are now on
the orchestrator path or explicitly demoted. The remaining blockers are
Telegram ingress, GS-owned runtime state, execution-adjacent OpenClaw seeding,
and the fact that GS still publishes compatibility wrapper contracts around
control state even after most first-party boolean readers converged on the
shared helper.

Use `docs/openclaw-demotion-plan.md` for the file-level retirement order of
`OpenClawStateDB` and adjacent runtime state, and
`docs/foundry-routing-adoption-plan.md` for the control-plane ingress and
Foundry/orchestrator routing prerequisites that have to land before the final
demotion steps can stick.

## 2. Approval And Control Flows With Remaining Migration Work

Tier-2 Telegram bot commands are **not** in this table because they are already
stubs. This table now separates what is already demoted from what is still
blocking a full GS-to-orchestrator cutover.

| Flow | Current code | Current behavior | Remaining issue |
| --- | --- | --- | --- |
| Shared GS task client boundary | `src/core/orchestrator_task_client.py:1-220`; `tests/core/test_orchestrator_task_client.py` | Exists and now provides `submit_task()`, `get_run()`, `get_run_history()`, `build_guarded_task_payload()`, and `submit_guarded_task()` for GS callers. | Landed, but runtime adoption is still partial and the orchestrator still needs GS-specific kind registration plus worker execution for end-to-end success. |
| Trade approval execution boundary | `src/execution/trade_approval.py:1-214,466-664` | Validates orchestrator approval context (`approval_jti`, `approval_reason`, `approval_exp`, scoped `target`, `ticket_id`, and required `ticket_hash` after `89310b8`), submits one guarded `gs.trade.execute_shadow` task, writes a JSONL audit mirror, and leaves `resolve_pending_approval()` / `get_pending_approvals()` as dead compatibility stubs. | Demoted off raw Telegram transport and local pending-file authority. The old `2bf35c5` payload-binding blocker is cleared, but the wider orchestrator-side GS kind routing and runtime adoption work is still open. |
| Position-manager approval handoff | `src/execution/position_manager.py:430-496`; `tests/execution/test_position_manager_manual_approval.py` | Proposed closes now carry `approval_required`, `ticket_id`, `ticket_hash`, `kind`, `target`, and `orchestrator_command` so each close can bind to a scoped GS approval token. | Handoff metadata is landed, but downstream execution still needs the same shared guarded submit/read path and orchestrator worker support. |
| Legacy approval-file API and frontend bridge | `dashboard/api/server.py:3025-3041`; `server.py:684-700`; `dashboard/frontend/src/lib/api.ts:263-290`; `dashboard/frontend/src/components/ExecutionModePanel.tsx:42-70` | `POST /api/telegram/approve` now returns `410 legacy_approval_file_bridge_disabled`; `GET /api/pending-orders` now returns an `approval_required` demoted payload; the frontend now tells operators these routes are no longer source of truth and points to orchestrator approval instead. | Demoted. Remaining gap is a real ticket submit/read UX backed by the task client rather than guidance text alone. |
| Execution-mode / kill-switch / manual-veto mutators | `dashboard/api/server.py:3004-3041,4031-4045,4995-5010`; `server.py:663-692,871-885`; `src/risk/manual_veto_mcp.py:127-163`; `scripts/ops/gs_control.py:62-196` | Dashboard/root API, MCP, and CLI mutators now return `approval_required` guidance and scoped orchestrator commands instead of writing local control files. | Mutation authority is demoted, but GS still has duplicated channel adapters and file-backed status reads. |
| Shared control snapshot adoption and remaining wrapper drift | `src/core/control_state_snapshot.py:1-32`; helper adopters in `scripts/ops/market_query.py:28,73`, `scripts/ops/daily_thesis_generator.py:22,74`, `scripts/healthcheck.py:24,74`, `scripts/ops/sentinel_status.py:27,55`, `src/reports/openclaw_role_briefing.py:12,99`, `src/risk/manual_veto_mcp.py:22,89-96`, `src/monitoring/crisis_monitor.py:38,193`, `scripts/agent_factory.py:53,143`, `src/execution/politician_alpha_executor.py:27,553`, and `scripts/self_improvement_loop.py:38,201`; compatibility wrappers in `server.py:317-333,767-783,906-928` and `dashboard/api/server.py:1861-1877,3933,4069-4091`; current-tree consumer convergence in `dashboard/frontend/src/lib/api.ts:96-108,358-430`, `dashboard/frontend/src/app/page.tsx:150,175,193,220-223,294-299`, `dashboard/frontend/src/components/ControlPanel.tsx:3-33`, `config/claude_cowork_mcp.json:86-99`, and `config/cowork_briefing_prompt.md:16-22` | Most first-party kill-switch / manual-veto readers now converge on `src/core/control_state_snapshot.py`; `GET /api/control/status` is the normalized operator-facing boolean surface; `GET /api/controls` and websocket `controls` wrappers now preserve outward metadata while normalizing booleans through the shared helper; and the active consumer-convergence lane in the current GS tree repoints the dashboard poll path and project-facing integration surfaces to `/api/control/status`. | Read-side drift is now concentrated in the remaining dual-contract behavior. The backend still publishes compatibility wrappers on `/api/controls` and websocket `controls`, while the frontend API layer still preserves wrapper-shape fallback for `{active}`-style payloads. The public operator-facing contract is narrower than before, but it is not fully singular yet. |

### Important nuance

- `src/execution/trade_approval.py` no longer owns Telegram callback polling
  or `OpenClawStateDB` as approval authority. It is now a fail-closed guarded
  submit boundary with compatibility stubs left behind for the dead file
  bridge.
- `2bf35c5` is now historical context rather than the live trade-approval
  blocker. `89310b8` tightened `request_approval()` to require the approved
  `ticket_hash`, and `7eb3b76` updates the companion Foundry routing doc to
  describe that cleared state accurately.
- `src/monitoring/telegram_command_handler.py` no longer calls
  `/api/telegram/approve` or the control mutator endpoints. The bot UX is ahead
  of the state authority behind it.
- `server.py`, `dashboard/api/server.py`, and the dashboard frontend no longer
  treat `/api/telegram/approve` or `/api/pending-orders` as live approval
  bridges, but they still do not submit real guarded tasks either.
- `server.py` and `dashboard/api/server.py` now normalize `GET /api/controls`
  and websocket `controls` booleans through the shared helper, but that only
  moved the drift boundary upward: the remaining inconsistency is now client
  and integration contract drift rather than raw file-key precedence inside
  first-party readers.
- In the current GS tree, the active consumer-convergence lane already narrows
  that drift: `dashboard/frontend/src/lib/api.ts` now fetches
  `/api/control/status` and normalizes it into `ControlStatus`,
  `dashboard/frontend/src/app/page.tsx` no longer binds websocket
  `data.controls`, `dashboard/frontend/src/components/ControlPanel.tsx` reads
  normalized booleans directly, and `config/claude_cowork_mcp.json` plus
  `config/cowork_briefing_prompt.md` now repoint project-facing integrations to
  `/api/control/status`.
- `src/execution/position_manager.py` now emits richer handoff metadata, but it
  still does not itself submit the guarded task or read back orchestrator run
  state.

### Remaining Control-Read Contract Drift

The main control-read problem is no longer that most first-party code still
opens `kill_switch.json` and `manual_veto.json` directly, and in the current
tree it is no longer the main dashboard poll path or the project-facing
config/prompt surfaces either. The higher-signal remaining drift is now the
dual public contract published above the shared helper:

- `server.py:317-333,767-783`
- `dashboard/api/server.py:1861-1877,3933`
- `dashboard/frontend/src/lib/api.ts:358-425`

The current GS tree already narrows the old client drift:

- `dashboard/frontend/src/lib/api.ts:419-430` now fetches
  `/api/control/status`
- `dashboard/frontend/src/app/page.tsx:171-176,190-194,220-223,294-299` now
  stores `ControlStatus` and no longer consumes websocket `data.controls`
- `dashboard/frontend/src/components/ControlPanel.tsx:14-33` now renders the
  normalized booleans directly
- `config/claude_cowork_mcp.json:86-99` and
  `config/cowork_briefing_prompt.md:16-22` now point project-facing
  integrations at `/api/control/status`

What still remains is subtler but still important:

- `GET /api/controls` and websocket `controls` frames are still published as
  compatibility wrappers
- `dashboard/frontend/src/lib/api.ts:363-417` still preserves wrapper-shaped
  `{active}` fallback when normalizing control payloads
- external consumers can therefore still observe or keep learning two public
  control-read shapes unless the compatibility wrapper is explicitly demoted,
  versioned, or retired

That means the migration is no longer just "replace writers" or even "move
first-party readers behind one helper." GS now also needs to finish collapsing
the public control-read contract so REST, websocket, dashboard, and
project-facing integrations stop carrying both normalized status and wrapper
compatibility semantics before read authority moves behind orchestrator.

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

Phase 5 now has three companion implementation docs that should be read with this
status note:

- `docs/openclaw-demotion-plan.md`
- `docs/foundry-routing-adoption-plan.md`
- `docs/gs-guarded-task-kinds-plan.md`

Their combined sequence is:

1. stabilize and verify the already-landed GS synchronous inference contract at
   `/v1/inference`
2. finish moving active GS runtime callers onto the shared routing boundary
3. register explicit GS task kind definitions and bind them to scoped targets
   such as `global-sentinel/control/kill-switch/on`,
   `global-sentinel/control/execution-mode/day_trade/manual`, and
   `global-sentinel/trade-ticket/<ticket_id>`
4. move OpenClaw runtime state and dispatch ownership out of
   `OpenClawStateDB` and `scripts/agent_factory.py`
5. collapse Tier-2 local mutators and execution-adjacent approvals into
   guarded orchestrator submission

The routing blockers are also shared across both plans:

- `/v1/inference` is no longer the blocker; the route is landed in
  orchestrator source, and the remaining gap is runtime adoption plus retiring
  GS fallback assumptions safely
- no GS-specific task kind registry or confirmed end-to-end worker routing path
  was found in orchestrator during this pass
- GS now has a shared task client boundary for `/v1/tasks` and `/v1/runs/*`,
  but it is only partially adopted by runtime callers
- local control files and `OpenClawStateDB` are still treated as authoritative

The key approval-token dependency is the same in both plans: guarded
replacement flows only work once GS actions can bind to an explicit task kind
plus a scoped target such as
`global-sentinel/control/execution-mode/day_trade/manual`,
`global-sentinel/control/manual-veto/on`, or
`global-sentinel/trade-ticket/<ticket_id>`. `docs/gs-guarded-task-kinds-plan.md`
defines the current GS target shapes. Without that narrower contract,
OpenClaw demotion cannot finish and Foundry/orchestrator routing cannot absorb
Tier-2 control or execution-capable GS paths safely. In practice, that means
the routing plan has to establish the task and ingress contract before the
demotion plan can retire the last GS-local approval and runtime-state
authorities.

### Concrete GS-side migration map

1. **Replace legacy approval transport with guarded task submission.**
   - `src/execution/trade_approval.py` has already moved onto this model. It
     no longer sends Telegram messages or polls `getUpdates`; it requires an
     orchestrator-approved context on entry and submits one scoped
     `gs.trade.execute_shadow` task through
     `src/core/orchestrator_task_client.py`.
   - `89310b8` clears the old `2bf35c5` payload-binding gap by requiring the
     approved `ticket_hash` on guarded submission, so the trade approval now
     binds to the reviewed payload instead of only the coarse task scope.
   - The current fail-closed boundary now validates:
     `kind`, `target`, `ticket_id`, `ticket_hash`, `approval_jti`,
     `approval_issued_by`, `approval_reason`, and `approval_exp`
     (or an equivalent orchestrator verdict object).
   - `src/execution/position_manager.py` now emits the per-ticket handoff
     metadata (`ticket_id`, `ticket_hash`, `kind`, `target`,
     `orchestrator_command`) needed to get proposed closes onto the same path.
   - Remaining gap: not every execution-capable GS caller uses the shared task
     client yet, and the orchestrator still needs GS kind registration plus a
     worker path that can execute these payloads end to end.

2. **Define real guarded task kinds for GS Tier-2 actions.**
   - At minimum GS needs orchestrator task kinds for:
     - execution mode changes
     - kill switch changes
     - manual veto changes
     - guarded trade execution or trade ticket preparation
   - Whatever names are chosen, they must be bound to scoped targets such as
     `global-sentinel/control/kill-switch/on` or
     `global-sentinel/trade-ticket/<ticket_id>` so the token verifier's
     `kind` and `target` checks are meaningful.
   - Today this is still a gap: no GS-specific guarded task kinds were found in
     the orchestrator source during this pass.

3. **Retire GS-local approval files as the source of truth.**
   - `POST /api/telegram/approve` and `GET /api/pending-orders` are already
     demoted. The backend no longer writes `pending_approval_*` or reads
     `pending_orders_*`, and the dashboard frontend no longer presents those
     routes as a live bridge.
   - Dashboard/root API control mutators, MCP mutators, and `gs_control.py`
     are also demoted to approval guidance instead of direct writes.
   - Short term, GS can keep a compatibility cache if existing readers still
     need file-backed inputs, but the remaining readers should be treated as
     migration debt rather than proof that the demoted write path should come
     back.
   - Long term, GS readers should consume orchestrator-derived state or a
     GS-local snapshot built from orchestrator verdicts.

4. **Collapse API, CLI, and MCP Tier-2 surfaces into one path.**
   - `dashboard/api/server.py`, `server.py`, `scripts/ops/gs_control.py`, and
     `src/risk/manual_veto_mcp.py` have already stopped acting as terminal
     mutators for the demoted lanes.
   - Remaining work is to decide which of those surfaces become:
     - read-only
     - removed
     - a real orchestrator submit/read client that records the returned
       `run_id`

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
   `wrkflo-orchestrator approve --kind <gs-kind> --target <gs-scoped-target> --reason "..."`
   where `<gs-scoped-target>` is an exact action scope such as
   `global-sentinel/control/kill-switch/on` or
   `global-sentinel/trade-ticket/<ticket_id>`
2. the caller submits one guarded `POST /v1/tasks` with:
   - `Authorization: Bearer <token>`
   - `kind`
   - `target: "<same scoped target>"`
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

rg -n "submit_task|build_guarded_task_payload|submit_guarded_task" \
  src/core/orchestrator_task_client.py

rg -n "request_approval|approval_jti|approval_exp|resolve_pending_approval|get_pending_approvals" \
  src/execution/trade_approval.py

rg -n "approval_required|ticket_id|ticket_hash|target|orchestrator_command" \
  src/execution/position_manager.py

rg -n "/api/telegram/approve|pending-orders|legacy_approval_file_bridge_disabled|approval_required|orchestrator_command" \
  dashboard/api/server.py server.py dashboard/frontend/src/lib/api.ts dashboard/frontend/src/components/ExecutionModePanel.tsx

rg -n "ORCHESTRATOR_APPROVAL_MESSAGE|Anthropic\\(|getUpdates|OpenClawStateDB|execution_enabled|requires_human_approval" \
  src/monitoring/telegram_command_handler.py \
  src/bridges/openclaw_research_bridge.py \
  scripts/agent_factory.py \
  src/core/openclaw_state_db.py

rg -n "pending_approval_|pending_orders_|kill_switch.json|manual_veto.json|set_manual_veto|set_kill_switch|clear_all_flags" \
  src scripts dashboard/api/server.py server.py
```

## Residual Risks

- GS now has the client/helper half of the guarded task path, but the
  orchestrator still needs confirmed GS-specific guarded kind registration and
  worker execution support. That is the main implementation gap after the docs
  work.
- `trade_approval.py` and `position_manager.py` now speak the right guarded
  approval vocabulary, but not every execution-capable GS path is using that
  boundary yet.
- `server.py`, `dashboard/api/server.py`, and the dashboard frontend now return
  demoted guidance rather than mutating approval state, but they still lack a
  first-class orchestrator submit/read UX.
- `server.py` and `dashboard/api/server.py` still duplicate the same demoted
  control surfaces and file-backed status reads, so migration drift is
  possible unless both are removed or unified.
- `trade_approval.py` appears retained rather than clearly wired from active
  runtime code today, which lowers immediate runtime risk but raises future
  regression risk if another path re-imports it before the migration is
  finished.
- Many GS readers still assume local control files are authoritative; replacing
  writers without replacing readers will leave the system split-brained.
- OpenClaw runtime state and execution-adjacent seeding still terminate inside
  GS, so approval demotion alone does not complete the overall migration.
