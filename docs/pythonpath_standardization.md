# PYTHONPATH Standardization

## Goal

Make every runtime entrypoint resolve the repo the same way, regardless of whether it is started by systemd, a local shell, a CI job, or the dashboard API.

## Canonical Contract

Use these values everywhere unless a task has an explicit reason to differ:

- Repo root: `/opt/global-sentinel`
- `GLOBAL_SENTINEL_REPO_ROOT=/opt/global-sentinel`
- `GS_REPO_ROOT=/opt/global-sentinel`
- `PYTHONPATH=/opt/global-sentinel`
- systemd `WorkingDirectory=/opt/global-sentinel`
- systemd user/group: `openclaw`

## Variable Roles

- `GLOBAL_SENTINEL_REPO_ROOT`
  - Primary repo-root contract for Python modules and scripts.
  - Most strategy, execution, monitoring, and research modules already default to this.
- `GS_REPO_ROOT`
  - Keep for the dashboard API and a few research/training entrypoints.
  - Set it to the same value as `GLOBAL_SENTINEL_REPO_ROOT`.
- `PYTHONPATH`
  - Required for systemd and non-repo-root invocations so imports resolve consistently.

## Standard Service Shape

Every long-running Python service should follow this pattern:

```ini
[Service]
User=openclaw
Group=openclaw
WorkingDirectory=/opt/global-sentinel
EnvironmentFile=-/opt/global-sentinel/.env
Environment=PYTHONPATH=/opt/global-sentinel
```

If the process is the dashboard API or another component that reads `GS_REPO_ROOT`, add:

```ini
Environment=GS_REPO_ROOT=/opt/global-sentinel
```

## Local Shell Contract

For local development or ad hoc execution from a checked-out repo:

```bash
export GLOBAL_SENTINEL_REPO_ROOT="$PWD"
export GS_REPO_ROOT="$PWD"
export PYTHONPATH="$PWD"
```

Then prefer repo-root invocations such as:

```bash
python3 -m dashboard.api.server
python3 src/monitoring/crisis_monitor.py --repo-root "$GLOBAL_SENTINEL_REPO_ROOT"
```

## CI / Automation Contract

- GitHub Actions and other automation should export the same three variables before invoking Python entrypoints.
- If a script already accepts `--repo-root`, pass it explicitly instead of relying on the current shell directory.
- Avoid job-specific path conventions such as `/home/gsadmin/global-sentinel`; treat those as legacy.

## Import Guidance

- Prefer absolute imports from the repo root.
- Prefer `python -m package.module` when the entrypoint supports it.
- Keep `sys.path.insert(...)` only as a temporary compatibility shim in thin wrappers.
- Do not add new ad hoc `sys.path` mutations in feature code when the environment can be standardized instead.

## Migration Rules

1. New systemd units must use `/opt/global-sentinel` for `WorkingDirectory`, `.env`, and `PYTHONPATH`.
2. New scripts should read repo-root config from `GLOBAL_SENTINEL_REPO_ROOT` first.
3. Dashboard-facing code should treat `GS_REPO_ROOT` as an alias of the same canonical root.
4. Legacy wrappers that patch `sys.path` should be documented as compatibility debt and removed once their caller exports the standard environment.

## Non-Goals

- This document does not change broker secret placement.
- This document does not define service ownership beyond the repo-root and import contract.
- This document does not override lane isolation or quantum policy rules.
