# GS Control Read Authority Plan

## Scope

This companion note refreshes the control-read migration lane after
`src/core/control_state_snapshot.py` adoption spread beyond the first
advisory callers, the dashboard/config consumer repoint landed, canonical
websocket `control_status` emission landed, the frontend live-consumer
convergence landed, and `12d5f23` centralized compatibility-wrapper metadata
shaping in the shared helper module.

The main remaining problem is no longer "the main dashboard poll path and live
websocket path still lack a canonical control payload." In the current GS
tree, those primary first-party consumers already point at the canonical
boolean contract.

The highest-signal remaining problem is narrower:

- GS still publishes explicit compatibility wrappers through
  `GET /api/controls` and websocket `controls`
- the generic frontend normalization helper still tolerates compatibility
  wrapper shapes, even though the live dashboard consumer no longer bridges
  `data.controls`
- `src/risk/manual_veto_mcp.py` remains a metadata sidecar above the boolean
  helper contract

This pass is docs-only. It does not change mutator authority and does not
widen the V1 helper contract defined in `docs/control-snapshot-contract.md`.

## Current Read Boundary

- `src/core/control_state_snapshot.py` is the current canonical boolean reader
  for `manual_veto` and `kill_switch`.
- That helper already normalizes:
  - explicit `manual_veto` over legacy `active`
  - explicit `kill_switch` over legacy `active`
  - missing, invalid, and non-object payloads to `False`
- `GET /api/control/status` is the canonical normalized operator-facing REST
  contract.
- Root websocket init/update frames in `server.py` and dashboard websocket
  init frames in `dashboard/api/server.py` now also emit canonical
  `control_status`.
- `GET /api/controls` and websocket `controls` payloads are explicit
  compatibility wrappers during the migration, not the preferred contract for
  new callers.
- Wrapper payload shaping is now centralized: `server.py:_controls_wrapper_payload()`
  and `dashboard/api/server.py:_controls_wrapper_payload()` delegate to
  `read_control_wrapper_snapshot(REPO_ROOT)` instead of doing their own raw
  `load_json(...)` reads.
- `execution_mode.yaml` remains outside the helper and is still read
  separately by status surfaces that need mode data.
- The main dashboard poll path, the main dashboard live consumer, and the
  project-facing config/prompt surfaces already point at the canonical
  control-status contract.

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
- `dashboard/frontend/src/app/page.tsx` now merges live control updates via
  `normalizeLiveControlStatusPayload(data)` rather than
  `data.control_status ?? data.controls`
- `dashboard/frontend/src/components/ControlPanel.tsx` now renders normalized
  booleans from `ControlStatus`
- `config/claude_cowork_mcp.json` and
  `config/cowork_briefing_prompt.md` now advertise `/api/control/status`

`src/risk/manual_veto_mcp.py:get_flags()` now keeps its boolean authority on
`read_control_state_snapshot(...)` and sources `*_updated_at` plus
`control_dir` through `read_control_metadata_snapshot(...)`, which keeps the
remaining metadata tail isolated from the server wrapper path.

That means the remaining work is now concentrated in explicit compatibility
wrappers plus the metadata sidecar in `manual_veto_mcp`, not in the main
dashboard or websocket live consumer path.

## Highest-Signal Remaining Tails

| Priority | Tail | Current behavior | Why it still matters | Recommended next step |
| --- | --- | --- | --- | --- |
| P0 | `server.py` and `dashboard/api/server.py` explicit compatibility wrappers (`GET /api/controls` + websocket `controls`) | Both server modules now publish canonical `control_status` and still publish compatibility `controls`. `_controls_wrapper_payload()` is centralized through `read_control_wrapper_snapshot(...)`, so the remaining issue is the continued public compatibility contract, not duplicate raw `load_json(...)` reads. | Primary first-party consumers now have canonical REST and live paths, but the compatibility wrapper still remains public and teaches legacy per-control `{active}`-style semantics. | Keep `control_status` canonical, document `/api/controls` and websocket `controls` as temporary compatibility wrappers, and set the narrowing or retirement boundary explicitly. |
| P1 | `dashboard/frontend/src/lib/api.ts` generic compatibility normalization | `fetchControlStatus()` already calls `/api/control/status`, and `dashboard/frontend/src/app/page.tsx` now consumes live updates only through `normalizeLiveControlStatusPayload(data)`, which reads `data.control_status`. The remaining compatibility logic is `selectControlStatusPayload(...)`, which still accepts `record.controls` and top-level wrapper objects on the generic normalization path. | This is no longer the main live-contract blocker, but it preserves client-side tolerance for compatibility shapes until the wrapper surfaces are retired. | After the wrapper retirement boundary is documented and covered, narrow or remove the generic wrapper fallback. |
| P1 | `src/risk/manual_veto_mcp.py:get_flags()` metadata sidecar | Booleans come from `read_control_state_snapshot(...)`; `manual_veto_updated_at`, `kill_switch_updated_at`, and `control_dir` come from `read_control_metadata_snapshot(...)`. | This is the main remaining first-party adapter that still depends on file-derived control metadata beyond the boolean contract. | Decide whether metadata belongs in a deliberate V2 helper contract or remains an isolated sidecar, and avoid spreading that metadata dependency elsewhere. |

## Adoption Sequence

1. Keep the V1 helper contract stable.
   - Continue treating `read_control_state_snapshot(...)` as the canonical
     boolean source for `manual_veto` and `kill_switch`.
   - Keep `execution_mode.yaml` separate until there is a deliberate contract
     change.

2. Keep `control_status` canonical across REST and live surfaces.
   - `GET /api/control/status` is the canonical normalized REST contract.
   - Root and dashboard websocket surfaces already emit `control_status`; do
     not add new first-party consumers to `controls`.

3. Bound the compatibility wrappers explicitly.
   - Treat `GET /api/controls` and websocket `controls` as temporary
     compatibility rails only.
   - Keep wrapper payload shaping centralized behind
     `_controls_wrapper_payload()` -> `read_control_wrapper_snapshot(...)`.
   - Do not widen wrapper semantics or add new clients that depend on them.

4. Retire frontend compatibility fallback after wrapper retirement is real.
   - The active live dashboard consumer is already canonical-only via
     `normalizeLiveControlStatusPayload(data)`.
   - Once explicit wrapper retirement is scheduled and covered, narrow
     `normalizeControlStatusPayload(...)` so it no longer needs to tolerate
     `record.controls` or wrapper-shaped payloads.

5. Decide the metadata boundary deliberately.
   - If callers need `*_updated_at`, `control_dir`, or related metadata,
     define where that data belongs.
   - Do not let metadata needs become a reason to keep expanding wrapper or
     direct-file contracts.

6. Swap backend authority behind the helper.
   - After wrapper publication and metadata sidecar boundaries converge,
     change `read_control_state_snapshot(...)` and any deliberate companion
     metadata helper from repo-local files to orchestrator-backed state.
   - Keep any temporary file fallback explicit and temporary inside the helper
     boundary only.

## Current Compatibility Boundary To Clear Next

The highest-signal contradiction is no longer the absence of a canonical live
payload. The canonical live payload already exists and the first-party
dashboard now consumes it. The remaining contradiction is parallel publication
of canonical and compatibility control contracts.

- `/api/control/status` is the canonical normalized REST contract for the main
  dashboard poll path, Telegram status reads, and project-facing config/prompt
  integrations.
- Root websocket init/update frames and dashboard websocket init frames now
  emit `control_status`.
- `dashboard/frontend/src/app/page.tsx` consumes live control data via
  `normalizeLiveControlStatusPayload(data)`, which only reads
  `data.control_status`.
- `GET /api/controls` and websocket `controls` still publish compatibility
  wrapper payloads built through `_controls_wrapper_payload()` and
  `read_control_wrapper_snapshot(...)`.
- `dashboard/frontend/src/lib/api.ts` still keeps generic compatibility
  normalization for wrapper-shaped payloads through
  `selectControlStatusPayload(...)`.
- `src/risk/manual_veto_mcp.py:get_flags()` is the remaining first-party
  metadata sidecar above the boolean helper contract.

Consequence:

- Primary first-party REST and live consumers now converge on canonical
  `control_status`.
- The remaining split-brain risk is concentrated in the explicit
  compatibility wrappers and any clients that keep depending on them, plus the
  sidecar metadata contract in `manual_veto_mcp`.

Required resolution:

- Keep `control_status` canonical across REST and websocket.
- Bound or retire `/api/controls` and websocket `controls` as temporary
  compatibility wrappers with documented metadata scope.
- Narrow or remove the generic frontend wrapper fallback after the wrapper
  retirement boundary is explicit and covered.
- Decide whether `manual_veto_mcp` metadata belongs in a helper contract or
  stays an isolated sidecar.
