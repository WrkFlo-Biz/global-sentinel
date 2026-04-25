# GS Control Snapshot Contract

## Scope

This note freezes the current normalized read contract behind
`src/core/control_state_snapshot.py` for GS readers that are moving off
hand-rolled control-file reads.

It is the contract companion to
`docs/control-read-authority-plan.md`, which inventories the remaining
readers and rollout order. This document defines the shared helper surface
those readers should converge on now, and it clarifies which published API
shape is canonical versus compatibility-only during the current migration.

This is a docs-only contract freeze. It does not change mutator authority,
does not move read authority to orchestrator yet, and does not widen the
current helper beyond what is already landed in code.

## Canonical Helper

Current shared boundary:

```python
read_control_state_snapshot(repo_root: Path | str) -> dict[str, bool]
```

Current canonical output:

```python
{
    "manual_veto": bool,
    "kill_switch": bool,
}
```

Current file inputs:

- `control/manual_veto.json`
- `control/kill_switch.json`

## Canonical Published API Contract

During the current migration, `/api/control/status` is the canonical
operator-facing control-read contract.

That contract is normalized around top-level booleans:

```json
{
  "kill_switch": true,
  "manual_veto": false
}
```

The real response may include additional status fields such as
`timestamp_utc`, `mode`, `cycle`, `regime_p`, `confidence`,
`shadow_eligible`, `fallback_mode`, `execution_mode`, and `evidence`, but
the control contract that downstream consumers should rely on is the
top-level normalized boolean pair:

- `kill_switch`
- `manual_veto`

Both root and dashboard API implementations source those booleans from
`read_control_state_snapshot(...)` before adding any broader status fields.

## Compatibility Wrappers During Migration

`/api/controls` and websocket `controls` payloads are still published during
the current migration, but they are compatibility wrappers, not the canonical
contract for new consumers.

Current compatibility surfaces:

- root API `GET /api/controls`
- dashboard API `GET /api/controls`
- root websocket `controls` field in init/update payloads
- dashboard websocket `controls` field in init payloads

Those wrapper payloads may still expose per-control objects with legacy
fields such as `active` and wrapper-level metadata, but their boolean values
must be derived from the shared helper rather than from ad hoc file-key
precedence in each surface.

## Current V1 Adopters In Source

As of the current GS source, the shared helper is already consumed by these
reader groups:

- Advisory readers:
  - `scripts/ops/market_query.py`
  - `scripts/ops/daily_thesis_generator.py`
- Status/reporting readers:
  - `scripts/healthcheck.py`
  - `scripts/ops/sentinel_status.py`
  - `src/reports/openclaw_role_briefing.py`
- Operator-visible status readers:
  - `server.py` `GET /api/control/status`
  - `dashboard/api/server.py` `GET /api/control/status`
- MCP read-only wrapper:
  - `src/risk/manual_veto_mcp.py:get_flags()`
- Runtime and execution-adjacent readers:
  - `src/monitoring/crisis_monitor.py`
  - `scripts/agent_factory.py`
  - `src/execution/politician_alpha_executor.py`
  - `scripts/self_improvement_loop.py`

This contract therefore needs to remain stable for both direct helper imports
and status surfaces that source their canonical booleans from the shared
helper. Compatibility wrappers may continue to exist temporarily, but they are
not the published shape new consumers should learn first.

## V1 Normalization Rules

1. Read each control file as a JSON object.
2. If the file is missing, invalid JSON, or not a JSON object, treat it as an
   empty payload.
3. For `manual_veto.json`, prefer the explicit `manual_veto` key when present.
4. For `kill_switch.json`, prefer the explicit `kill_switch` key when present.
5. If the explicit key is absent, fall back to legacy `active`.
6. If neither key is present, default that flag to `False`.
7. The helper always returns normalized booleans only; it must not leak raw
   payload shape to callers.

## Important Precedence Rule

If both shapes are present in the same file, the explicit key wins.

Examples:

```json
{"manual_veto": true, "active": false}
```

Normalizes to:

```json
{"manual_veto": true}
```

And:

```json
{"kill_switch": false, "active": true}
```

Normalizes to:

```json
{"kill_switch": false}
```

This rule exists so newer explicit-key payloads are not silently overridden by
stale legacy compatibility fields.

## Current Remaining Contradiction

The highest-signal remaining contract contradiction is now a dual payload
shape above the shared helper:

- backend compatibility wrappers still publish wrapper-shaped payloads through
  `/api/controls` and websocket `controls`
- the frontend normalization layer in
  `dashboard/frontend/src/lib/api.ts:normalizeControlStatusPayload(...)`
  still accepts both:
  - the canonical normalized boolean shape from `/api/control/status`
  - the legacy wrapper shape where booleans are inferred from `{active}`

This dual-shape acceptance is a temporary migration bridge only. It does not
change the canonical contract:

- `/api/control/status` remains the normalized boolean contract
- `/api/controls` and websocket `controls` remain compatibility wrappers

Until the compatibility wrapper is explicitly retired, versioned, or more
tightly bounded, GS still exposes two public control-read shapes even though
both now derive their boolean truth from the same helper.

## Reader Guardrails

- New GS readers that only need kill-switch and manual-veto booleans should
  call `read_control_state_snapshot(...)` instead of opening control files
  directly.
- New HTTP/API consumers should prefer `/api/control/status` and its
  top-level normalized booleans.
- Callers must not re-implement legacy `active` fallback or explicit-key
  precedence locally. Compatibility logic belongs in one place only.
- Callers must treat `active` as an input compatibility detail, not as part of
  the exported shared contract.
- Callers must treat `/api/controls` and websocket `controls` as
  compatibility-only surfaces during the migration, not as the contract to
  copy for new integrations.
- The shared helper is currently a boolean snapshot only. Callers should not
  widen its return shape ad hoc for one surface.
- Surface-specific response fields such as `manual_veto_updated_at`,
  `kill_switch_updated_at`, or `control_dir` remain wrapper-level concerns
  unless a coordinated helper contract change is explicitly planned.
- `execution_mode.yaml` remains outside this helper for now. Current status/API
  readers may continue their existing execution-mode read path until a separate
  coordinated contract change lands.

## Migration Guardrails

1. Keep all new direct boolean readers on the shared helper.
   - If a reader only needs kill-switch and manual-veto booleans, it should
     import `read_control_state_snapshot(...)` rather than opening control
     files directly.

2. Keep wrapper and API contracts stable while sourcing booleans from the
   helper.
   - `/api/control/status` is the canonical published boolean contract.
   - `/api/controls` and websocket `controls` may continue temporarily as
     compatibility wrappers, but they should be documented and treated as
     compatibility-only.
   - Only the boolean source of truth should converge here; do not bundle
     helper adoption with mutator or approval-contract changes.

3. Prefer normalized booleans for downstream API consumers too.
   - Indirect readers that consume `/api/control/status` should use the
     normalized `kill_switch` and `manual_veto` booleans from that response.
   - Do not teach new consumers to depend on wrapper-shaped `/api/controls`
     or websocket `controls` payload semantics.
   - Frontend-side wrapper fallback is a temporary bridge, not a contract
     precedent for new callers.

4. Keep execution mode separate until there is a coordinated contract change.
   - `execution_mode.yaml` is still outside this helper.
   - Readers that need execution mode may continue their current file-backed
     execution-mode path while using the helper for control booleans.

5. Swap backend authority only after reader convergence is complete.
   - When orchestrator-backed read authority arrives, change the implementation
     behind `read_control_state_snapshot(...)`, not every caller.
   - Any temporary file-backed fallback should stay inside the helper boundary.

## Test Requirements For Contract Changes

Any change to the shared helper or its read semantics should keep focused
coverage for:

- explicit-key precedence over `active`
- legacy `active` fallback
- missing files defaulting to `False`
- invalid JSON defaulting to `False`
- non-object JSON payloads defaulting to `False`

If the helper surface is widened in the future, add contract tests for the new
fields before migrating more readers.

## Non-Goals

- This contract does not make GS-local files the desired long-term authority.
- This contract does not expand the helper to include execution mode,
  timestamps, source metadata, or raw payload mirrors today.
- This contract does not retire `/api/controls` or websocket `controls`
  wrappers immediately; it only marks them as compatibility-only during the
  migration.
- This contract does not change any control mutator endpoint, MCP guidance, or
  approval flow.
