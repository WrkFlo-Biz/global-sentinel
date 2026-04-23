# OpenClaw Demotion

## Purpose

This note audits the current OpenClaw-linked surfaces that still live inside
`global-sentinel`, separates what is already advisory-only from what still acts
like a control surface, and frames the migration needed to demote OpenClaw to a
bounded downstream channel.

The target boundary follows the GS architecture split in
`docs/architecture-delta-gs-view.md:20-41` and the repo ownership split in
`docs/cross-project-routing.md:37-49`: Global Sentinel owns domain logic and
execution guardrails, `wrkflo-orchestrator` owns control-plane mediation, and
OpenClaw becomes a downstream client/channel rather than a parallel operator
surface.

## Current State

### Already demoted to advisory-only

| Surface | Current code | What it does now | Target posture |
| --- | --- | --- | --- |
| OpenClaw role registry | `config/openclaw_role_registry.yaml:1-58`, `src/core/openclaw_role_registry.py:12-70` | All configured roles are typed as `paper_only`; only `chief_of_staff` has `telegram_updates: true`. | Keep in GS as configuration for advisory roles only. |
| Role briefing artifacts | `src/reports/openclaw_role_briefing.py:54-80` | Builds role artifacts with `paper_only`, `no_live_orders`, and `no_promotion_authority`, then writes JSON briefs. | Keep in GS as Tier-1 advisory output. |
| Recommendation queue | `src/reports/openclaw_recommendation_queue.py:27-62` | Appends `role_advisory` entries with `manual_approval_required`, `replay_required`, `paper_only`, and `live_execution_forbidden`. | Keep in GS as a human-gated advisory queue. |
| Advisory tests | `tests/test_openclaw_role_briefing.py:47-132`, `tests/test_openclaw_recommendation_queue.py:7-35` | Tests assert paper-only safety stamps and manual approval constraints. | Keep as regression coverage for the demoted boundary. |

### Still coupled to Telegram or OpenClaw runtime authority

| Surface | Current code | What GS is doing today | Why this is still too strong | Proposed replacement after demotion |
| --- | --- | --- | --- | --- |
| Telegram research relay | `src/bridges/openclaw_research_bridge.py:5-21`, `src/bridges/openclaw_research_bridge.py:128-163`, `src/bridges/openclaw_research_bridge.py:173-230`, `src/bridges/openclaw_research_bridge.py:292-320` | GS sends `/gs_research` prompts to Telegram, polls `getUpdates`, and normalizes bot replies into bridge events. | GS is still directly coupled to Telegram transport and OpenClaw bot identity for research overflow. | Replace with orchestrator task submission such as `POST /v1/tasks` for `research_overflow`, then poll `GET /v1/runs/{id}` for the result envelope. |
| Telegram command bot launcher | `scripts/start_telegram_bots.py:40-97`, `src/monitoring/telegram_bot_manager.py:37-123` | GS starts long-polling handlers for `mo2darkbot` and `mo2drkbot` and binds them to GS strategies. | This keeps OpenClaw-linked bots as first-class operator entry points inside GS. | Move bot-facing control transport out of GS; leave only read-only status or advisory fan-out in this repo. |
| Telegram command handler | `src/monitoring/telegram_command_handler.py:98-115`, `src/monitoring/telegram_command_handler.py:396-482`, `src/monitoring/telegram_command_handler.py:613-704` | `/mode` changes execution mode, `/approve` and `/reject` write approval intents through the dashboard, `/kill` and `/veto` write control files directly, and `/refresh` signals the crisis monitor. | OpenClaw-linked chat surfaces still mutate runtime state and approval state. | Replace with orchestrator-owned approval/control APIs; Telegram should become a notification or approval transport only, not the state writer. |
| Dashboard write endpoints behind Telegram | `server.py:605-650`, `server.py:833-859` | GS exposes API endpoints that rewrite `config/execution_mode.yaml`, `control/pending_approval_*.json`, `control/kill_switch.json`, and `control/manual_veto.json`. | Even though `/api/*` can require `GS_DASHBOARD_API_KEY` (`server.py:28-48`), the approval and control decision still terminates inside GS rather than the orchestrator. | Accept signed orchestrator verdicts or approval tokens instead of direct mutable control requests. |
| OpenClaw agent runtime in GS | `scripts/agent_factory.py:52-61`, `scripts/agent_factory.py:406-455`, `scripts/agent_factory.py:1010-1248` | GS imports OpenClaw role plumbing, runs a long-lived `OpenClawBot`, seeds tasks, and writes OpenClaw result artifacts locally. | OpenClaw still exists as an embedded runtime lane inside GS instead of a bounded downstream client. | Reduce GS to role-brief generation only, or move the OpenClaw bot loop to the OpenClaw-owned runtime repo. |
| Proposal review guardrails | `scripts/agent_factory.py:360-403` | Proposal reviews are written with `requires_human_approval: false` and `execution_enabled: true`. | This is stronger than an advisory-only demoted posture. | Flip proposal-review outputs to explicit advisory-only payloads or require an orchestrator approval token before any downstream execution path can consume them. |
| Strategy execution seeding | `scripts/agent_factory.py:846-983`, `scripts/agent_factory.py:1185-1193` | `strategy_executor` can route ideas into `TradeIdeaPackager -> ShadowOrderRouter -> broker adapter`, and the seeding comment explicitly calls out live-order-pipeline risk. | This is incompatible with a demoted OpenClaw role inside GS. | Remove executor seeding from OpenClaw-owned flows; let orchestrator own any execution escalation. |
| Crypto execution seeding | `scripts/agent_factory.py:1195-1202` | Research bot seeding still includes `crypto_executor` on cadence. | Another OpenClaw-owned path that crosses out of pure advisory work. | Move to a GS-native scheduler not associated with OpenClaw, or require orchestrator-issued execution approval. |
| OpenClaw data feed | `scripts/ops/openclaw_data_feed.py:43-52`, `scripts/ops/openclaw_data_feed.py:72-123`, `scripts/ops/openclaw_data_feed.py:180-275` | GS compiles account, positions, trades, signals, and volatility data into `openclaw_live_feed.json` and `openclaw_summary.txt`. | This is acceptable only if it remains strictly outbound/read-only; it should not become a control backchannel. | Keep as an outbound summary feed or republish through orchestrator-managed transport. |
| Placeholder OpenClaw search hook | `src/bridges/unified_search_bridge.py:103-111` | A placeholder `search_openclaw()` path exists but currently returns `[]` and is not active. | Not an active authority path today, but it shows intent to keep OpenClaw as a search fallback. | If revived, route it through orchestrator task submission rather than direct OpenClaw gateway ownership in GS. |

## Current vs Target State

### Current

- OpenClaw role artifacts are already bounded and paper-only.
- GS still owns Telegram polling, Telegram command handling, OpenClaw bot task
  seeding, and an OpenClaw-specific research relay.
- Some OpenClaw-adjacent paths can still influence execution state or approval
  state from inside GS.

### Target

- OpenClaw remains only a downstream consumer of GS-generated advisory artifacts
  and optional read-only summaries.
- `wrkflo-orchestrator` owns task submission, approval transport, and any
  control-plane interaction that crosses from advisory into action.
- GS keeps only advisory generation and domain-state computation; it does not
  host OpenClaw as a parallel operator runtime.

## Migration Plan

1. Freeze OpenClaw-owned execution entry points inside GS.
   - Stop treating `scripts/agent_factory.py` as an execution-capable OpenClaw
     runtime.
   - Remove `strategy_executor` and `crypto_executor` from OpenClaw-owned task
     seeding.

2. Replace Telegram relay research with orchestrator tasks.
   - Convert `OpenClawResearchBridge.fetch()` from Telegram send/poll behavior
     into a client that submits a bounded research request and receives a routed
     result envelope.
   - Keep the same normalized `events` contract on the GS side so downstream
     bridges do not need a second migration.

3. Strip Telegram command handlers down to Tier-0/Tier-1 behavior.
   - Remove `/mode`, `/kill`, `/veto`, `/approve`, `/reject`, and `/refresh`
     from OpenClaw-linked bot handlers.
   - Retain read-only commands like `/status`, `/portfolio`, `/orders`, and
     `/alerts` only if chat access is still needed.

4. Move control-plane writes behind orchestrator mediation.
   - `server.py` should stop directly accepting state-changing bot commands as
     the terminal authority.
   - GS should instead consume an orchestrator verdict payload that carries
     operator identity, approval scope, and run ID.

5. Keep only bounded OpenClaw outputs in GS.
   - Role briefs, recommendation queue entries, and optional outbound summaries
     stay.
   - Embedded OpenClaw runtime ownership does not.

## Verification Commands

Use these commands after migration work to confirm the demotion boundary:

```bash
cd /home/moses/projects/global-sentinel

rg -n "OpenClawResearchBridge|TelegramCommandHandler|OpenClawBot|OPENCLAW_ENABLE_STRATEGY_EXECUTOR" \
  src scripts

rg -n "kill_switch.json|manual_veto.json|pending_approval_|/api/execution-mode|/api/telegram/approve" \
  src scripts server.py

pytest -q tests/test_openclaw_role_briefing.py tests/test_openclaw_recommendation_queue.py -p no:cacheprovider
```

Expected end state:

- The first grep should only show advisory/reporting or compatibility-shim
  surfaces, not OpenClaw-owned execution or command control loops.
- The second grep should no longer show OpenClaw-linked Telegram paths mutating
  control or approval state.
- The tests should still prove that retained OpenClaw artifacts stay paper-only
  and human-gated.
