# OpenClaw Demotion

## Purpose

This is the Global Sentinel side of the OpenClaw demotion audit. It updates the
older handoff assumptions to match the current tree and current control-plane
reality:

- current GS files live under `src/monitoring`, `src/bridges`,
  `src/execution`, `scripts/ops`, `scripts/systemd`, and `server.py`
- the old `src/integrations/*` path story is stale for this repo
- `wrkflo-orchestrator` is the intended control-plane owner
- OpenClaw should end up as a downstream channel/client, not a parallel control
  runtime embedded inside GS

Pair this with:

- `wrkflo-orchestrator/docs/openclaw-demotion.md`
- `wrkflo-orchestrator/docs/current-runtime-truth.md`

Replacement targets in this doc are aligned to the orchestrator surface that is
actually documented as live today: `POST /v1/tasks`, `GET /v1/runs/{id}`,
`GET /v1/runs/{id}/history`, `GET /v1/foundry/roles`, and scoped workspace
reads. Do not assume a live `/v1/workers` or suspended-run `/approve` endpoint;
`current-runtime-truth.md` explicitly shows those are not the current runtime.

## What Changed Since The Stale Handoff

- Telegram Tier-2 bot commands are no longer mutating GS state directly.
  `src/monitoring/telegram_command_handler.py:40-47,429-447,578-618` now
  returns an orchestrator-approval stub for `/gs_mode`, `/gs_kill`,
  `/gs_veto`, `/gs_approve`, `/gs_reject`, and `/gs_refresh`.
- A shared GS orchestrator task boundary now exists in
  `src/core/orchestrator_task_client.py`; guarded submissions can carry
  `project`, `target`, requester identity, and approval context instead of
  each module hand-rolling that contract.
- `src/execution/trade_approval.py` is no longer a Telegram/local pending-file
  approval authority. It validates orchestrator approval context, fails closed
  when that context is missing or stale, and submits the guarded
  `gs.trade.execute_shadow` task through `wrkflo-orchestrator`.
- `src/execution/position_manager.py` now emits orchestrator approval handoff
  metadata for guarded close proposals, and the dashboard/backend
  `GET /api/pending-orders` plus `POST /api/telegram/approve` bridge assumptions
  are demoted in `dashboard/api/server.py`, `server.py`,
  `dashboard/frontend/src/lib/api.ts`, and
  `dashboard/frontend/src/components/ExecutionModePanel.tsx`.
- The Telegram bot surface still exists and is still GS-owned.
  `src/monitoring/crisis_monitor.py:122-129` starts
  `TelegramBotManager`, and `src/monitoring/telegram_bot_manager.py:37-123`
  still long-polls `mo2darkbot` / `mo2drkbot`.
- The stronger remaining control couplings are now the GS-owned Telegram/chat
  runtime, the Telegram research relay, localhost control wrappers in
  `scripts/ops/gs_control.py`, and the embedded OpenClaw runtime in
  `scripts/agent_factory.py`.

## Current State

### Already bounded to advisory/read-only use

| Surface | Current code | What GS does now | Keep / change |
| --- | --- | --- | --- |
| OpenClaw role registry | `config/openclaw_role_registry.yaml:1-58`, `src/core/openclaw_role_registry.py:13-68` | Roles are typed and currently configured as `paper_only`; only selected roles request Telegram updates. | Keep in GS as advisory-role configuration. |
| Role briefing artifacts | `src/reports/openclaw_role_briefing.py`, used from `scripts/agent_factory.py:407-430` | Builds role artifacts with paper-only and no-promotion guardrails, then queues advisory output. | Keep in GS as bounded advisory generation. |
| Recommendation queue | `src/reports/openclaw_recommendation_queue.py`, used from `scripts/agent_factory.py:426-430` | Appends human-gated `role_advisory` queue entries. | Keep in GS if it remains advisory-only. |
| Telegram Tier-2 bot demotion tests | `tests/test_telegram_command_handler.py:32-60` | Tests assert Tier-2 bot commands do not hit legacy mutation paths or write control files. | Keep as regression coverage. |
| Trade approval fail-closed tests | `tests/execution/test_trade_approval_fail_closed.py:151-312` | Tests assert guarded approval blocks on missing/stale orchestrator approval context and that the legacy Telegram/local pending-file bridge stays disabled. | Keep as regression coverage for the orchestrator-mediated fail-closed path. |
| OpenClaw outbound data feed | `scripts/ops/openclaw_data_feed.py:43-52,180-275`, `scripts/systemd/gs-openclaw-feed.service:1-10` | Builds `openclaw_live_feed.json` and `openclaw_summary.txt` from GS/account state for downstream consumption. | Keep only if it stays strictly one-way and read-only. |

### Already demoted to orchestrator mediation

| Surface | Current code | What GS does now | Keep / change |
| --- | --- | --- | --- |
| Shared GS task client boundary | `src/core/orchestrator_task_client.py:45-110,225-238` | Centralizes GS task submission with normalized `project`, `target`, requester, and approval metadata for guarded work. | Keep as the shared GS-to-orchestrator contract boundary. |
| Trade approval orchestration path | `src/execution/trade_approval.py:3-7,41-46,299-337,454-594,646-665` | Validates `approval_jti` / `approval_exp`, fails closed on missing or stale context, and submits the guarded execution task through orchestrator rather than Telegram callbacks or local pending files. | Keep and harden as the GS-side guarded submission path. |
| Position-manager approval handoff | `src/execution/position_manager.py:430-496` | Emits `approval_required`, target, and orchestrator command metadata for guarded close proposals instead of relying on a GS-local pending-order bridge. | Keep as proposal shaping only; orchestrator remains the approval source of truth. |
| Demoted pending-orders and legacy approval bridge UX | `dashboard/api/server.py:3025-3041`, `server.py:684-700`, `dashboard/frontend/src/lib/api.ts:263-290`, `dashboard/frontend/src/components/ExecutionModePanel.tsx:1-116` | `POST /api/telegram/approve` and `GET /api/pending-orders` now return demoted/orchestrator-guidance payloads, and the dashboard frontend shows explicit orchestrator approval guidance instead of approve/reject or pending-order bridge UX. | Keep demoted until operator-facing approval UX moves fully behind orchestrator-owned surfaces. |

### Still coupled to Telegram or OpenClaw control/runtime authority

| Surface | Current code | What GS is doing today | Why this is still too strong | Replacement target |
| --- | --- | --- | --- | --- |
| GS-owned Telegram bot surface | `src/monitoring/crisis_monitor.py:122-129`, `src/monitoring/telegram_bot_manager.py:37-123`, `scripts/start_telegram_bots.py:40-97` | GS starts and owns long-polling Telegram handlers for `mo2darkbot` and `mo2drkbot`. | Even with Tier-2 commands stubbed, GS still owns bot tokens, polling, operator chat UX, and free-form LLM chat transport. | Move Telegram/OpenClaw channel ownership behind `wrkflo-orchestrator`; GS should not host the bot transport loop. |
| Telegram command dispatcher | `src/monitoring/telegram_command_handler.py:40-97,304-333,385-606` | Read commands still call GS dashboard APIs, and non-`/gs_` messages still route to in-handler LLM chat. Tier-2 commands now return an orchestrator stub instead of mutating local state. | Better than the old state, but GS still acts as the chat-facing control surface rather than a downstream domain service. | Replace chat submission with orchestrator task envelopes via `POST /v1/tasks`; render results from `GET /v1/runs/{id}` / `/history`. |
| Telegram research relay | `src/bridges/openclaw_research_bridge.py:5-21,128-163,173-320` | GS sends `/gs_research` messages over Telegram and polls `getUpdates` for OpenClaw bot replies. | GS is directly coupled to Telegram transport and OpenClaw bot identity for research overflow. | Submit a bounded research task to orchestrator with `POST /v1/tasks`, then poll `GET /v1/runs/{id}` for the result envelope. |
| GS-hosted control/approval endpoint namespace | `dashboard/api/server.py:2946-2988,3025-3041,3993-4015`, `server.py:632-700` | GS still serves the `/api/execution-mode`, `/api/control/kill-switch`, `/api/control/veto`, `POST /api/telegram/approve`, and `GET /api/pending-orders` routes, but they now return orchestrator guidance/demoted responses instead of mutating local control or pending-order files. | Better than the old file authority, but operator-facing approval/control UX still terminates on GS-hosted routes rather than an orchestrator-owned surface. | Move operator-facing approval/control UX behind orchestrator-owned APIs or UI; keep GS endpoints read-only or retire them entirely. |
| Localhost remote-control CLI | `scripts/ops/gs_control.py:1-20,147-203` | OpenClaw-facing CLI still targets the GS dashboard control routes even though those routes are now demoted guidance surfaces. | The CLI preserves a direct OpenClaw-to-GS control lane and stale API assumptions, even if the endpoints no longer write files. | Retire or reduce to read-only status wrappers; if retained, it should target orchestrator tasks instead of GS-hosted control endpoints. |
| Embedded OpenClaw runtime in GS | `scripts/agent_factory.py:361-404,1000-1060,1242-1320,1385-1405`, `scripts/systemd/global-sentinel-openclaw-ops.service:1-35`, `scripts/systemd/global-sentinel-openclaw-research.service:1-37` | GS still runs `OpenClawBot` workers, queueing, state DB writes, advisory generation, and execution-adjacent tasks as GS-owned services. | OpenClaw is still a first-class runtime lane inside GS instead of a bounded downstream channel. | Move runtime/channel ownership to the OpenClaw/orchestrator side; keep only advisory artifact production in GS. |
| Execution-capable OpenClaw seeding | `scripts/agent_factory.py:372-399,847-984,1299-1316` | Proposal reviews still emit `requires_human_approval: false` and `execution_enabled: true`; research seeding can run `strategy_executor`, and always seeds `crypto_executor` on cadence. `strategy_executor` routes into `TradeIdeaPackager -> ShadowOrderRouter -> broker adapter`. | This crosses out of advisory mode and into execution-capable OpenClaw-owned flows. | Flip proposal-review output to explicit advisory-only metadata and move execution escalation behind orchestrator approval and routing. |
| Placeholder OpenClaw search hook | `src/bridges/unified_search_bridge.py:5-12,103-111` | Current `search_openclaw()` is a stub that returns `[]`; it is not active authority today. | Low current risk, but it preserves intent to re-introduce an OpenClaw-owned search fallback. | If revived, it should submit orchestrator tasks instead of targeting an OpenClaw gateway directly from GS. |

## Current Boundary Summary

### Current GS reality

- advisory OpenClaw role outputs are already mostly demoted
- a shared `src/core/orchestrator_task_client.py` boundary now exists for GS
  task submission
- `trade_approval.py` is fail-closed and orchestrator-mediated rather than a
  Telegram/local pending-file approval authority
- `position_manager.py` now carries orchestrator approval handoff metadata for
  guarded close proposals
- `GET /api/pending-orders`, `POST /api/telegram/approve`, and the dashboard
  frontend bridge are demoted to orchestrator-guidance UX, not local approval
  state
- GS still owns Telegram bot polling and chat handling
- GS still hosts the dashboard approval/control endpoint namespace, even though
  the legacy approval bridges are demoted
- GS still embeds OpenClaw runtime/service ownership through `agent_factory.py`

### Target boundary

- OpenClaw becomes a channel adapter behind `wrkflo-orchestrator`
- `wrkflo-orchestrator` owns task submission, approval mediation, and audit trail
- Global Sentinel keeps domain logic, advisory artifacts, and optional one-way
  status/output feeds
- GS stops being the terminal writer for OpenClaw/Telegram-initiated control
  actions

## Migration Order

1. Move Telegram/OpenClaw chat submission to orchestrator.
   - Replace direct Telegram relay logic in
     `src/bridges/openclaw_research_bridge.py` and the GS-owned bot loop with
     `POST /v1/tasks`.
   - Read task state back through `GET /v1/runs/{id}` and
     `GET /v1/runs/{id}/history`.

2. Keep trade approval on orchestrator mediation and remove residual GS-hosted
   approval UX.
   - `src/execution/trade_approval.py` is already aligned to the orchestrator
     beta: guarded work is submitted once with approval context on
     `POST /v1/tasks`.
   - Remaining work is to keep approval UX/operator transport out of GS-owned
     Telegram or dashboard bridges.

3. Finish retiring GS-hosted control/approval bridge surfaces.
   - `POST /api/telegram/approve` and `GET /api/pending-orders` are already
     demoted.
   - The remaining GS-hosted control endpoint namespace and
     `scripts/ops/gs_control.py` should move behind orchestrator-owned control
     surfaces instead of acting as the operator entrypoint.

4. De-embed OpenClaw runtime from GS.
   - Stop treating `scripts/agent_factory.py` plus the
     `global-sentinel-openclaw-*` units as the long-term OpenClaw runtime.
   - Keep role-brief and recommendation generation if they remain advisory-only.

5. Keep only bounded one-way outputs in GS.
   - role briefs
   - recommendation queue entries
   - optional outbound summaries like `openclaw_data_feed`

## Verification Commands

Use these read-only checks after migration work:

```bash
cd /home/moses/projects/global-sentinel

rg -n "TelegramBotManager|OpenClawResearchBridge|request_approval|OpenClawBot|gs_control" \
  src scripts server.py dashboard/api/server.py

rg -n "/api/execution-mode|/api/telegram/approve|/api/control/kill-switch|/api/control/veto" \
  server.py dashboard/api/server.py scripts/ops/gs_control.py

pytest -q \
  tests/test_telegram_command_handler.py \
  tests/execution/test_trade_approval_fail_closed.py \
  tests/test_openclaw_role_briefing.py \
  tests/test_openclaw_recommendation_queue.py \
  -p no:cacheprovider
```

Expected end state:

- the first grep shows only bounded advisory/reporting surfaces, not Telegram
  transport ownership or embedded OpenClaw runtime
- the second grep may still show compatibility routes during transition, but
  they should return orchestrator-guidance/demoted responses rather than mutate
  GS-local approval or control files
- tests still prove:
  - Tier-2 bot commands do not mutate local state
  - retained OpenClaw artifacts stay advisory-only
  - approval flows fail closed and use orchestrator mediation as the source of
    truth
