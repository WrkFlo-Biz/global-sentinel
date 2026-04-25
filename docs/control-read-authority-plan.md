# GS Control Read Authority Plan

## Scope

This companion note refreshes the control-read migration lane after
`src/core/control_state_snapshot.py` adoption spread beyond the first
advisory callers, the dashboard/config consumer repoint landed, and the newer
contract docs were refreshed.

The main remaining problem is no longer "the main dashboard poll path and
project-facing config/prompt surfaces still point at `/api/controls`." In the
current GS tree, those primary consumers already point at
`/api/control/status`.

The highest-signal remaining problem is narrower:

- GS still publishes explicit compatibility wrappers through
  `GET /api/controls` and websocket `controls`
- the frontend normalization bridge still accepts both the canonical boolean
  shape and the legacy wrapper `{active}` shape while the live-update contract
  finishes converging
- a smaller tail of metadata adapters and isolated direct readers still matter
  before backend authority swaps

This pass is docs-only. It does not change mutator authority and does not
widen the V1 helper contract defined in `docs/control-snapshot-contract.md`.

## Current Read Boundary

- `src/core/control_state_snapshot.py` is the current canonical boolean reader
  for `manual_veto` and `kill_switch`.
- That helper already normalizes:
  - explicit `manual_veto` over legacy `active`
  - explicit `kill_switch` over legacy `active`
  - missing, invalid, and non-object payloads to `False`
- `GET /api/control/status` is now the canonical normalized operator-facing
  control-read contract.
- `GET /api/controls` and websocket `controls` payloads are compatibility
  wrappers during the migration, not the preferred contract for new callers.
- `execution_mode.yaml` remains outside the helper and is still read
  separately by status surfaces that need mode data.
- In the current GS tree, the main dashboard poll path and the
  project-facing config/prompt surfaces already point at
  `/api/control/status`.

## Readers Already Converged On The Shared Helper

These readers already source kill-switch and manual-veto booleans from
`read_control_state_snapshot(...)`:

- Advisory readers:
  - `scripts/ops/market_query.py`
  - `scripts/ops/daily_thesis_generator.py`
- Status and reporting readers:
  - `scripts/healthcheck.py`
  - `scripts/ops/sentinel_status.py`
  - `src/reports/openclaw_role_briefing.py`
- Operator-visible status readers:
  - `server.py` `GET /api/control/status`
  - `dashboard/api/server.py` `GET /api/control/status`
- Runtime and execution-adjacent readers:
  - `src/monitoring/crisis_monitor.py`
  - `scripts/agent_factory.py`
  - `src/execution/politician_alpha_executor.py`
  - `scripts/self_improvement_loop.py`
- MCP read wrapper booleans:
  - `src/risk/manual_veto_mcp.py:get_flags()`

Indirect operator and project-facing consumers have also already shifted away
from `/api/controls` for their primary status path:

- `scripts/ops/gs_control.py status` reads `GET /api/control/status`
- `src/monitoring/telegram_command_handler.py:_cmd_status()` reads
  `GET /api/control/status`
- `dashboard/frontend/src/lib/api.ts` now fetches
  `GET /api/control/status` through `fetchControlStatus()`
- `dashboard/frontend/src/app/page.tsx` now uses `api.controlStatus()` for the
  main dashboard poll path
- `dashboard/frontend/src/components/ControlPanel.tsx` now renders normalized
  booleans from `ControlStatus`
- `config/claude_cowork_mcp.json` and
  `config/cowork_briefing_prompt.md` now advertise `/api/control/status`

That means the remaining work is now concentrated in explicit compatibility
wrappers, the frontend's temporary dual-shape bridge, and a smaller
metadata/direct-reader tail rather than in the main dashboard or config/prompt
consumers.

## Highest-Signal Remaining Tails

| Priority | Tail | Current behavior | Why it still matters | Recommended next step |
| --- | --- | --- | --- | --- |
| P0 | `server.py` and `dashboard/api/server.py` `GET /api/controls` | `_controls_wrapper_payload()` reads normalized booleans from the shared helper, but still opens the raw control JSON files with `load_json(...)` and republishes wrapper fields such as `active` plus file-shaped metadata. | This keeps a second public control contract alive. Even though the booleans are normalized, clients can still learn wrapper semantics instead of the canonical `/api/control/status` contract. | Keep `/api/controls` explicitly compatibility-only, then either remove it or re-emit a tightly bounded compatibility payload with documented metadata rules and an end-of-life path. |
| P0 | Root and dashboard websocket `controls` payloads | Root websocket init/update payloads and dashboard websocket init payloads still ship `controls: _controls_wrapper_payload()`. No backend websocket surface emits a canonical `control_status` field today. | Live consumers still receive the compatibility wrapper by default even though the REST poll path has converged on `/api/control/status`. | Emit a canonical websocket `control_status` payload or version the live message shape explicitly, then bound or retire websocket `controls`. |
| P0 | Frontend temporary dual-shape normalization bridge in `dashboard/frontend/src/lib/api.ts` and `dashboard/frontend/src/app/page.tsx` | The frontend REST path now fetches `/api/control/status`, but `normalizeControlStatusPayload(...)` still accepts both top-level normalized booleans and nested wrapper records via legacy `{active}` fallback. The websocket merge path still reads `data.control_status ?? data.controls`. | The dashboard no longer teaches `/api/controls` as primary, but it still encodes compatibility semantics. That means the published live contract is not fully singular yet. | Once a canonical websocket payload exists or wrapper retirement is scheduled, remove wrapper fallback from the frontend normalization layer and delete the `?? data.controls` bridge. |
| P1 | `src/risk/manual_veto_mcp.py:get_flags()` metadata sidecar | The booleans already come from the shared helper, but timestamp fields still come from raw reads of `manual_veto.json` and `kill_switch.json`, and the adapter still exposes `control_dir`. | This is the main remaining first-party read surface that still opens the raw control payloads for meaning rather than simple diagnostics. | Decide whether `set_at` and related metadata belong in a coordinated V2 helper contract or remain a local adapter concern, but keep that choice isolated instead of spreading more raw file reads. |
| P2 | Isolated direct readers and file-presence diagnostics | `scripts/ops/iran_war_intelligence.py` still keeps a direct `kill_switch.json` path. `scripts/verify/system_integrity_check.sh` and `scripts/self_improvement_loop.py` still check control-file presence/readability as deployment diagnostics. | Lower blast radius, but they still preserve GS-local files as an operational truth source or implicit health contract around the helper. | Migrate the direct kill-switch read to the helper and keep file-existence checks explicitly framed as deployment diagnostics rather than state authority. |

## Adoption Sequence

1. Keep the V1 helper contract stable.
   - Continue treating `read_control_state_snapshot(...)` as the canonical
     boolean source for `manual_veto` and `kill_switch`.
   - Keep `execution_mode.yaml` separate until there is a deliberate contract
     change.

2. Keep `/api/control/status` as the canonical published read contract.
   - The main dashboard poll path, Telegram status reads, and project-facing
     config/prompt integrations are already aligned here.
   - Do not reintroduce `/api/controls` as the primary contract for new
     consumers.

3. Finish live contract convergence above the helper.
   - Bound `/api/controls` as an explicit compatibility wrapper.
   - Emit a canonical websocket `control_status` payload or version the live
     message shape explicitly.
   - Once that canonical live path exists, remove the frontend's
     dual-shape fallback and `data.controls` bridge.

4. Decide the metadata boundary deliberately.
   - If callers need `set_at`, `control_dir`, or related metadata, define where
     that data belongs.
   - Do not let timestamp or metadata needs become a reason for new callers to
     reopen the raw control files directly.

5. Sweep the remaining low-signal tails.
   - Keep config and prompt integrations on `/api/control/status`.
   - Migrate isolated direct readers such as
     `scripts/ops/iran_war_intelligence.py`.
   - Keep file-presence checks only where they are truly deployment-health
     diagnostics.

6. Swap backend authority behind the helper.
   - After the public wrapper and live client contracts converge, change
     `read_control_state_snapshot(...)` from repo-local files to
     orchestrator-backed state.
   - Keep any temporary file fallback explicit and temporary inside the helper
     boundary only.

## Live Contract Contradiction To Clear First

The highest-signal contradiction is now the dual payload-shape contract above
an otherwise converged boolean reader.

- `/api/control/status` is already the canonical normalized REST contract for
  the main dashboard poll path, Telegram status reads, and project-facing
  config/prompt integrations.
- `GET /api/controls` and websocket `controls` still publish wrapper-shaped
  compatibility payloads built around per-control objects.
- `dashboard/frontend/src/lib/api.ts` still normalizes both:
  - the canonical top-level boolean shape
  - the legacy wrapper shape where booleans are inferred from `{active}`
- `dashboard/frontend/src/app/page.tsx` still bridges live updates through
  `data.control_status ?? data.controls`

Consequence:

- There is now one canonical REST contract for primary consumers, but there is
  not yet one singular live contract across REST and websocket.
- If orchestrator-backed read authority lands before the wrapper path is
  bounded, split-brain risk moves from "which file key wins" to "which live
  payload shape the consumer still tolerates."

Required resolution:

- Keep `/api/control/status` canonical.
- Keep `/api/controls` and websocket `controls` explicitly compatibility-only
  and temporary.
- Add a canonical websocket `control_status` path or version the live message
  shape explicitly.
- Remove frontend wrapper fallback once the canonical live path exists.
