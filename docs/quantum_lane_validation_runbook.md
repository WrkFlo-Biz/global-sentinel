# Quantum Lane Validation Runbook

## Purpose

This runbook validates the hardened QPanda3 research lane on the VM without
changing runtime maturity or widening automation. The goal is to confirm that
the research slice is usable, metadata-complete, and still fully isolated from
execution.

## Scope

Validation covers:

- `pyqpanda3` environment readiness
- experiment registry loading
- `CPUQVM` local deterministic path
- optional `QCloudService` path
- optional `QPilotService` path
- async polling metadata
- timeout simulation and partial-artifact persistence
- artifact completeness for research-only flags and execution metadata

Validation does not cover:

- Stage 2 or Stage 3 quantum promotion
- execution-path influence
- broker routing
- canary policy changes
- stabilization timer changes

## Commands

Run the bounded validation report locally:

```bash
python3 scripts/ops/run_quantum_lane_validation.py
```

Run it on the VM with optional backend execution when credentials are present:

```bash
python3 scripts/ops/run_quantum_lane_validation.py --execute-qcloud --execute-pilot
```

Default output path:

```text
reports/operational/quantum_lane_validation_report.json
```

## How To Read The Report

Key sections:

- `environment_status`
  Confirms package availability and credential presence.
- `policy_status`
  Confirms QPanda3-only posture and Stage 1-only restrictions remain active.
- `backend_validation`
  Shows whether `cpuqvm`, `qcloud`, and `pilot` were executed, skipped, or fell back.
- `experiment_family_validation`
  Confirms registry resolution across the bounded finance experiment families.
- `timeout_and_partial_artifact_validation`
  Verifies timeout handling and partial-artifact metadata completeness.
- `policy_compliance_assessment`
  Confirms the quantum lane remains artifact-only and execution-disabled.

## PilotOS Posture

PilotOS is optional and human-facing only.

Recommended uses:

- visual circuit inspection
- interactive debugging
- compilation sanity checks
- task-status inspection

Do not use PilotOS as:

- a production runtime dependency
- a replacement for VM-based artifact persistence
- a policy bypass
- an execution-path input

## Acceptance Criteria

The validation wave is healthy when:

- `pyqpanda3` is present on the VM or the absence is explicit and understood
- CPUQVM path is validated directly or reports a clear fallback reason
- QCloud and Pilot paths are either validated directly or marked clearly as not configured
- artifacts include `artifact_only`, `research_only`, and `not_for_direct_execution`
- execution metadata includes backend, provider, framework, and shot information
- timeout simulation writes a complete partial artifact
- Stage 1 remains the only active maturity stage
