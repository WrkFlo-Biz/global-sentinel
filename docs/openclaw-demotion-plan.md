# OpenClaw Demotion Plan

## Scope

This note is the Phase 5 OpenClaw demotion audit for the GS-owned SQLite state
helper in [`src/core/openclaw_state_db.py`](/home/moses/projects/global-sentinel/src/core/openclaw_state_db.py).

This pass covered:

- every direct import or method call that touches `OpenClawStateDB`
- schema-level readers of the same `state.db` tables that bypass the import
- concrete replacement targets in `wrkflo-orchestrator`

This pass did **not** change runtime code. No inseparable cleanup was required.

Pair this with:

- [`docs/openclaw-demotion.md`](/home/moses/projects/global-sentinel/docs/openclaw-demotion.md)
- [`docs/migration-status.md`](/home/moses/projects/global-sentinel/docs/migration-status.md)
- `wrkflo-orchestrator/docs/openclaw-demotion.md`
- `wrkflo-orchestrator/src/wrkflo_orchestrator/state.py`

## What `openclaw_state_db` Owns Today

`src/core/openclaw_state_db.py` currently mixes three separate concerns:

| OpenClaw slice | Current GS implementation | Replacement owner | Replacement path |
| --- | --- | --- | --- |
| Runtime task state | `task_history` via `record_task_start()` and `record_task_status()` | `wrkflo-orchestrator` | Use orchestrator run lineage in `SQLiteStateStore.append()` / `latest()` / `history()` for in-flight state, and `SQLiteStateStore.record_task_completion()` for terminal summaries. Do not port GS-local `running` / `requeued` / `dead_letter` rows 1:1. |
| Worker liveness | `worker_health` via `update_worker_health()` | `wrkflo-orchestrator` | Use `OrchestratorService.worker_heartbeat()` with `SQLiteStateStore.record_worker_health()` and `worker_health()`. |
| Approval audit mirror | `audit_log` via `append_audit_log()` and legacy migration from `trade_approval_audit` | `wrkflo-orchestrator` as authority, optional GS-local JSONL mirror | Approval authority should move to orchestrator `approval_tokens_used` plus `audit_events` via `consume_approval_token()` and `log_audit_event()`. In the current tree, `src/execution/trade_approval.py` already uses a narrow JSONL mirror keyed by approval metadata rather than GS-local sqlite authority. |

## Module Retirement Map

These are the responsibilities inside
[`src/core/openclaw_state_db.py`](/home/moses/projects/global-sentinel/src/core/openclaw_state_db.py)
that should be split during demotion.

| Function or method | Lines | Current role | Disposition | Concrete replacement path |
| --- | --- | --- | --- | --- |
| `default_state_db_path()` | `39-43` | Resolves GS-local `state.db` or `OPENCLAW_STATE_DB_PATH` | Remove | GS should stop resolving a repo-local OpenClaw DB path. Runtime state should live in `wrkflo-orchestrator`'s `default_state_db_path()` or behind its HTTP API. |
| `ensure_audit_log_schema()` | `46-83` | Creates GS-local approval audit table and indexes | Remove | Approval audit belongs in orchestrator `approval_tokens_issued`, `approval_tokens_used`, `approval_tokens_revoked`, and `audit_events`. |
| `migrate_legacy_trade_approval_audit()` | `108-182` | One-time migration from `trade_approval_audit` into `audit_log` | Remove after archival | If legacy rows still matter, do a one-shot export/archive, not a permanent runtime migration path inside GS. |
| `OpenClawStateDB.ensure_schema()` | `200-238` | Creates all three local state tables | Split then remove | Runtime and approval state should be owned by orchestrator tables in `wrkflo_orchestrator.state.SQLiteStateStore._init_db()`. |
| `OpenClawStateDB.schema_snapshot()` | `240-252` | Returns GS-local schema metadata for migration checks | Remove | No steady-state GS equivalent. If an inspection tool is still needed, it should inspect orchestrator state directly. |
| `OpenClawStateDB.record_task_start()` | `258-281` | Writes `task_history` start/running rows | Move to orchestrator state model | Starting/running state should become orchestrator run snapshots (`append()`, `latest()`, `history()`) rather than GS-local mutable rows. |
| `OpenClawStateDB.record_task_status()` | `283-308` | Writes terminal or retry status into `task_history` | Move to orchestrator state model | Terminal summaries should use `record_task_completion()`; retry/dead-letter state should be represented in run history or handled by orchestrator retry policy, not GS-local rows. |
| `OpenClawStateDB.update_worker_health()` | `310-331` | Writes worker liveness/current task rows | Move to orchestrator state | Use `OrchestratorService.worker_heartbeat()` -> `SQLiteStateStore.record_worker_health()`. |
| `OpenClawStateDB.append_audit_log()` | `333-360` | Stores structured approval events in local SQLite | Replace with orchestrator audit authority plus optional narrow local log | Use orchestrator approval-token consumption and `log_audit_event()` for the durable decision trail; keep only JSONL mirror logging in GS if richer payload capture is still required. |

## Direct Runtime Call Sites

Only two runtime files currently depend directly on `OpenClawStateDB`.

### `scripts/agent_factory.py`

[`scripts/agent_factory.py`](/home/moses/projects/global-sentinel/scripts/agent_factory.py)
is the largest remaining runtime dependency. Its database writes are not
incidental; they are how GS still behaves like an embedded OpenClaw runtime.

| Function or method | Lines | Current dependency | What it does today | Disposition | Concrete replacement path |
| --- | --- | --- | --- | --- | --- |
| `OpenClawBot.__init__()` | `1012-1037` | Instantiates `OpenClawStateDB(default_state_db_path(REPO_ROOT))` | Boots a GS-owned OpenClaw runtime against repo-local `state.db` | Remove from GS runtime | Stop constructing `OpenClawStateDB` in GS. Long term, `OpenClawBot` should disappear from GS; short term, any remaining bridge should submit tasks to `POST /v1/tasks` and retain only orchestrator `run_id`s. |
| `OpenClawBot._spawn_worker()` | `1043-1055` | Calls `update_worker_health(... status="starting")` | Marks a new local worker as starting in `worker_health` | Move to orchestrator state if worker survives, otherwise remove | If a temporary bridge worker remains, emit orchestrator worker heartbeat instead of local SQLite writes. Otherwise remove with the GS-hosted worker pool. |
| `OpenClawBot._mark_task_started()` | `1057-1071` | Calls `record_task_start()` and `update_worker_health(... status="busy")` | Marks a task running and binds it to a local worker row | Move to orchestrator state | Replace with orchestrator run creation and in-flight lineage: `POST /v1/tasks` plus orchestrator `state_snapshots`. Busy worker state should come from orchestrator heartbeat, not GS. |
| `OpenClawBot._mark_task_result()` | `1073-1094` | Calls `record_task_status(... completed/failed)` and idle worker update | Persists task outcome in `task_history` and clears worker | Move to orchestrator state | Use orchestrator `record_task_completion()` for terminal summaries if GS still hosts a bridge worker. Long term remove when GS stops executing OpenClaw work. |
| `OpenClawBot._mark_task_requeued()` | `1096-1111` | Calls `record_task_status(... requeued)` and idle worker update | Persists local retry bookkeeping | Move to orchestrator state model | Retry state should be represented by orchestrator run history or resubmission semantics, not GS-local `requeued` rows. |
| `OpenClawBot._worker_loop()` | `1126-1192` | Calls `update_worker_health()` on idle heartbeat and final stop | Maintains liveness in local SQLite while queue workers run inside GS | Move to orchestrator state if any worker remains, otherwise remove | Use orchestrator worker heartbeat if there is still a worker process. Preferred end state is no GS-owned OpenClaw worker loop at all. |
| `OpenClawBot._dead_letter()` | `1207-1238` | Calls `record_task_status(... dead_letter)` and worker idle update | Persists terminal local failure state for dropped tasks | Move to orchestrator state model | Terminal failure should become orchestrator run history plus `record_task_completion(success=False)` or a failed run snapshot, not a GS-local dead-letter row. |
| `OpenClawBot.stop()` | `1365-1374` | Calls `update_worker_health(... status="stopping")` | Marks local workers as stopping during shutdown | Move to orchestrator state if any worker remains, otherwise remove | Emit a final orchestrator worker heartbeat only if a bridge worker still exists. Preferred end state is removal with the embedded runtime. |

### `src/execution/trade_approval.py` (already demoted from this lane)

[`src/execution/trade_approval.py`](/home/moses/projects/global-sentinel/src/execution/trade_approval.py)
is no longer a direct `OpenClawStateDB` dependency in the current tree. The
trade-approval boundary already validates guarded orchestrator context, submits
one scoped `gs.trade.execute_shadow` task, and keeps only a local JSONL audit
mirror.

| Function | Lines | Current behavior | Current demotion read |
| --- | --- | --- | --- |
| `_log_approval_json()` | `122-128` | Appends approval events to `logs/trade_approvals.jsonl` | The retained local mirror is file-based only; there is no sqlite fan-out here. |
| `_record_approval_event()` | `131-164` | Builds structured `approval_requested` / `approval_decision` rows and writes them through `_log_approval_json()` | Approval-event logging already bypasses `OpenClawStateDB`; keep this helper only as a narrow JSONL mirror unless GS drops the file entirely. |
| `request_approval()` | `468-647` | Validates guarded approval context, enforces fail-closed checks, and submits one orchestrator-mediated guarded task | This migration step is already landed. Remaining cleanup is dead-code and documentation retirement around old sqlite assumptions, not a runtime approval-flow rewrite. |

That means the remaining approval-audit demotion work now lives in
`src/core/openclaw_state_db.py` and any archival/migration helpers that still
describe `audit_log` as active authority. It does **not** live in the current
`trade_approval.py` runtime path anymore.

### `scripts/ops/migrate_state_db.py`

| Function | Lines | Current dependency | What it does today | Disposition | Concrete replacement path |
| --- | --- | --- | --- | --- | --- |
| `main()` | `15-32` | Imports `OpenClawStateDB` and `default_state_db_path()`, then prints `schema_snapshot()` | Creates/upgrades GS-local OpenClaw `state.db` on demand | Remove after demotion | If historical data must be preserved, replace this with a one-shot archival/export script. There should be no ongoing GS migration script for an OpenClaw-owned DB once demotion is complete. |

## Test-Only Dependencies

These are not production call sites, but they will need cleanup once the
runtime dependencies move.

### Direct import tests

| Function | Lines | Current dependency | Disposition | Concrete replacement path |
| --- | --- | --- | --- | --- |
| `tests/test_openclaw_state_db.py::test_state_db_schema_contains_task_worker_and_audit_tables()` | `30-45` | Imports `OpenClawStateDB` and asserts GS-local schema columns | Remove from GS | Equivalent schema ownership should be tested in `wrkflo-orchestrator`, not in GS. |
| `tests/test_openclaw_state_db.py::test_migrate_state_db_script_is_idempotent()` | `48-73` | Executes `scripts/ops/migrate_state_db.py` against a temp `state.db` | Remove with script, or replace with one-shot archive test if export tool remains | No steady-state GS test should depend on provisioning OpenClaw `state.db`. |
| `tests/test_openclaw_state_db.py::test_state_db_appends_structured_audit_log_rows()` | `76-110` | Verifies `append_audit_log()` writes into `audit_log` | Replace or remove | If GS keeps a narrow local JSONL mirror, test that mirror instead. Approval DB semantics belong in orchestrator tests. |
| `tests/test_openclaw_state_db.py::test_state_db_migrates_legacy_trade_approval_rows_into_audit_log()` | `113-188` | Verifies migration from `trade_approval_audit` into `audit_log` | Remove after archival | Keep only as a temporary archive/export test if legacy data still needs one final extraction. |
| `tests/test_openclaw_state_db.py::test_agent_factory_records_task_history_and_worker_health()` | `191-262` | Uses `OPENCLAW_STATE_DB_PATH`, then asserts `task_history` and `worker_health` rows | Replace or remove | If a GS-to-orchestrator bridge remains, test the emitted client payloads or orchestrator contract, not sqlite rows in GS. Otherwise remove with `OpenClawBot`. |

### Trade-approval tests already reflect the demoted flow

The current trade-approval tests are no longer `OpenClawStateDB` cleanup work.
They already assert JSONL-only local logging and explicit absence of a GS-local
sqlite authority:

| Function | Lines | Current behavior | Current demotion read |
| --- | --- | --- | --- |
| `tests/execution/test_trade_approval_fail_closed.py::_set_paths()` | `41-47` | Patches `APPROVAL_LOG_PATH` and the legacy `PENDING_DIR` only | No sqlite path injection remains in the approval module test setup. |
| `tests/execution/test_trade_approval_fail_closed.py::_audit_entries()` | `56-64` | Reads `trade_approvals.jsonl` entries only | Unit coverage already validates the retained JSONL mirror rather than `audit_log` rows. |
| `tests/execution/test_trade_approval_fail_closed.py::_assert_terminal_audit()` | `67-108` | Asserts JSONL parity and explicitly checks that `state.db` was **not** created | This is the opposite of the stale demotion assumption: the test now proves sqlite authority is gone from the trade-approval path. |
| `tests/test_integration.py::test_trade_request_approval_and_execution_logging_flow()` | `247-314` | Asserts guarded submit payloads plus JSONL audit metadata such as `run_id` | Integration coverage is already aligned with orchestrator-mediated approval and local JSONL mirroring. |
| `tests/test_integration.py::test_rejected_trade_request_stops_before_execution_logging()` | `317-350` | Asserts JSONL rejection logging and absence of downstream execution logs | No sqlite approval dependency remains in the rejected-path coverage either. |

Keep these tests in GS unless the retained JSONL mirror is removed entirely.
They are no longer blockers for `OpenClawStateDB` retirement.

## Recommended Migration Sequence

1. Stop treating `state.db` as the source of truth for OpenClaw runtime state.
   - Remove `OpenClawBot`'s direct `OpenClawStateDB` construction first.
   - Any surviving worker bridge should report liveness via orchestrator worker
     heartbeat, not GS-local sqlite.

2. Move task lifecycle truth to orchestrator runs.
   - Replace `record_task_start()` / `record_task_status()` semantics with
     orchestrator run creation and run history.
   - Keep terminal summaries in orchestrator `task_completions`, not GS-local
     `task_history`.

3. Treat trade-approval sqlite demotion as already landed and finish the
   residual cleanup.
   - `src/execution/trade_approval.py` already uses guarded orchestrator submit
     plus `logs/trade_approvals.jsonl`; do not reopen that runtime path as if it
     still wrote `audit_log` rows.
   - The remaining approval-audit cleanup is dead-code, archival, and
     documentation work around `src/core/openclaw_state_db.py`, not another
     trade-approval flow rewrite.

4. Remove GS-only migration and schema tests.
   - Delete `scripts/ops/migrate_state_db.py` after any one-shot archival work.
   - Replace or remove GS tests that assert sqlite rows directly.

5. Delete `src/core/openclaw_state_db.py` only after the above callers are gone.
   - The module should be removed last, after both runtime and tests have been
     re-pointed.

## Excluded References

These files reference `openclaw_state_db` in prose only and were **not** treated
as code dependencies in this plan:

- [`docs/migration-status.md`](/home/moses/projects/global-sentinel/docs/migration-status.md)
- [`docs/openclaw-demotion.md`](/home/moses/projects/global-sentinel/docs/openclaw-demotion.md)
- `memory/2026-04-24.md`

## Verification Commands

```bash
cd /home/moses/projects/global-sentinel

rg -n "openclaw_state_db|OpenClawStateDB" .
sed -n '1,260p' docs/openclaw-demotion-plan.md
```
