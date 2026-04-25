# GS Control Wrapper Retirement Plan

## Scope

This note is the docs-only follow-on to the current control-read migration
lane. It covers the retirement or explicit versioning sequence for the
remaining compatibility layer above
`src/core/control_state_snapshot.py`.

In scope:

- `GET /api/controls`
- websocket `controls` payloads
- frontend `normalizeControlStatusPayload(...)`
- the historical live-update bridge expressed as
  `data.control_status ?? data.controls`

This note does not change mutator authority, does not widen the helper
contract in `docs/control-snapshot-contract.md`, and does not move backend
authority to orchestrator yet.

## Current State

Canonical read contract today:

- `GET /api/control/status` is the canonical normalized REST contract
- top-level boolean fields are:
  - `kill_switch`
  - `manual_veto`

Compatibility surfaces that still remain:

- root API `GET /api/controls`
- dashboard API `GET /api/controls`
- root websocket `controls` payloads in init/update frames
- dashboard websocket `controls` payloads in init frames

Frontend bridge behavior today:

- `dashboard/frontend/src/lib/api.ts` still keeps a compatibility bridge in
  `normalizeControlStatusPayload(...)`
- the current selector path accepts:
  - a canonical nested websocket payload under `control_status`
  - a compatibility nested websocket payload under `controls`
  - a direct canonical `/api/control/status` payload with top-level booleans
  - legacy wrapper records where booleans are inferred from `{active}`

Important nuance:

- the older explicit app-level bridge
  `data.control_status ?? data.controls` has effectively been absorbed into
  the selector logic inside `normalizeControlStatusPayload(...)`
- the retirement problem is therefore behavioral, not just syntactic:
  the frontend still tolerates both canonical and compatibility wrapper
  shapes during live updates

## Target End State

The end state should leave one clear control-read contract per transport:

- REST: `/api/control/status` remains canonical
- websocket: a canonical `control_status` payload exists and is the only
  control-state shape first-party live consumers are expected to parse
- frontend normalization accepts only canonical control-state shapes
- `/api/controls` and websocket `controls` are either:
  - retired completely, or
  - frozen as explicitly versioned compatibility surfaces with a bounded
    end-of-life path

The important constraint is that wrapper retirement must happen before or in
lockstep with any backend authority swap. Otherwise the system keeps two
public payload shapes even if both derive their booleans from one helper.

## Recommended Sequence

### Phase 0: Freeze The Compatibility Boundary

Document and enforce these rules first:

- `/api/control/status` is canonical
- `/api/controls` is compatibility-only
- websocket `controls` is compatibility-only
- no new consumer should be taught the wrapper shape
- no new fields should be added only to the wrapper path unless they are
  explicitly marked compatibility metadata

This phase is mostly about preventing the dual-shape surface from growing
again while the retirement work is staged.

### Phase 1: Introduce A Canonical Live Contract

Before removing any frontend fallback, the backend needs a canonical live
shape for websocket consumers.

Required backend outcome:

- websocket frames that currently emit `controls` must also emit a canonical
  `control_status` payload, or move to a clearly versioned live message shape
- that canonical live payload should match `/api/control/status` semantics for:
  - `kill_switch`
  - `manual_veto`
  - any agreed status metadata such as `mode`, `cycle`, `regime_p`,
    `confidence`, `shadow_eligible`, `fallback_mode`, `execution_mode`,
    and `evidence`

Gate to leave this phase:

- first-party live consumers can read control state without depending on the
  wrapper object shape

### Phase 2: Keep Frontend Compatibility Temporary And Explicit

Once a canonical live field exists, the frontend bridge should narrow in two
steps.

Step 1:

- keep `normalizeControlStatusPayload(...)` accepting canonical payloads first
- keep compatibility parsing only as a temporary migration bridge
- treat wrapper support as transitional code, not as a stable API promise

Step 2:

- remove the effective `data.control_status ?? data.controls` bridge behavior
  from live-update handling
- in current-tree terms, that means removing selector support for websocket
  `controls` once canonical `control_status` is reliably present

At the end of this phase, the frontend should still be able to normalize:

- direct `/api/control/status` payloads
- canonical websocket `control_status` payloads

It should no longer need to infer booleans from wrapper `{active}` records for
first-party live updates.

### Phase 3: Decide Wrapper Retirement Versus Versioning

Preferred outcome:

- retire `GET /api/controls`
- stop emitting websocket `controls`

Fallback if external compatibility pressure remains:

- keep the wrapper path, but mark it as explicit compatibility v1 behavior
- freeze the wrapper shape
- publish an end-of-life window
- forbid new first-party consumers from depending on it

The key rule is that "compatibility" must become an explicit product decision,
not an accidental permanent second contract.

### Phase 4: Remove Legacy Shape Parsing

After canonical live websocket payloads are stable and wrapper retirement or
versioning is decided:

- remove wrapper `{active}` fallback from
  `normalizeControlStatusPayload(...)`
- remove selector support for legacy `controls` payloads if the wrapper is
  retired
- keep normalization only for the canonical top-level boolean shape and any
  intentionally supported canonical websocket envelope

This is the point where the frontend stops carrying dual-shape tolerance as
normal behavior.

### Phase 5: Backend Authority Swap Becomes Safe

Only after the public wrapper contract is bounded should backend read
authority move from repo-local files to orchestrator-backed state.

At that point:

- `read_control_state_snapshot(...)` can change implementation without forcing
  contract-specific migrations in every caller
- contract drift is limited to one canonical shape instead of two public ones

## Prerequisites Before Removing Wrapper Fallback

Do not remove wrapper fallback until all of these are true:

1. `/api/control/status` remains the canonical documented REST surface.
2. The main dashboard poll path already uses `/api/control/status`.
3. Project-facing config and prompt integrations already point at
   `/api/control/status`.
4. A canonical websocket `control_status` payload exists, or a versioned live
   replacement is in place.
5. First-party live consumers no longer need websocket `controls`.
6. Any remaining compatibility clients are either migrated or explicitly
   carried as a versioned compatibility population.

## Retirement Decision Rule

Choose full retirement when:

- all known first-party consumers use canonical REST and canonical live
  payloads
- there is no remaining operational reason to preserve wrapper objects
- compatibility can be handled by a bounded migration window rather than a
  permanent second contract

Choose explicit versioning when:

- unknown or external consumers still depend on wrapper payloads
- removing the wrapper immediately would create avoidable operational risk
- the team is willing to carry a frozen compatibility surface temporarily with
  clear ownership and an end date

## Success Criteria

- `/api/control/status` is the only documented canonical control-read API
  surface
- websocket control state has one canonical live payload shape
- first-party frontend code no longer tolerates wrapper `{active}` shape as a
  normal path
- `/api/controls` and websocket `controls` are either retired or clearly
  versioned compatibility surfaces
- a backend authority swap can happen without preserving dual public control
  contracts
