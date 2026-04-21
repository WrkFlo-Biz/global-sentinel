# Legacy Service Cleanup Plan

## Purpose

Retire repo-era systemd units that still assume `gsadmin` and `/home/gsadmin/global-sentinel` after the canonical `openclaw` + `/opt/global-sentinel` units are confirmed healthy on the VM.

This is a runbook only. Do not delete or edit unit files until the live VM inventory is complete and Claude's back-port of VM-only units/configs has landed in the repo.

## Canonical Runtime Target

- User: `openclaw`
- Repo root: `/opt/global-sentinel`
- Primary units:
  - `global-sentinel.service`
  - `global-sentinel-reconciler.service`
  - `global-sentinel-openclaw-ops.service`
  - `global-sentinel-openclaw-research.service`
  - `global-sentinel-dashboard.service`

## Legacy Cleanup Candidates

These repo-shipped units still point at `gsadmin` and `/home/gsadmin/global-sentinel` and should be treated as decommission targets once the canonical units are verified:

- `openclaw-ops.service`
- `openclaw-research.service`
- `healthcheck.service`
- `healthcheck.timer`
- `self-improvement-loop.service`

Do not auto-clean these without review:

- `gs-strategy-trainer.service`
  - Uses `/opt/global-sentinel` and `openclaw`; it is not a legacy `gsadmin` unit.
- Any VM-only units Claude is currently back-porting
  - Claude reported roughly 80 VM services not yet represented in the repo. Hold those until the SCP sync is committed.

## Why These Units Are Risky

- They can run duplicate workloads beside the newer `global-sentinel-*` units.
- They still bind to the old path and user model:
  - `User=gsadmin`
  - `WorkingDirectory=/home/gsadmin/global-sentinel`
  - `EnvironmentFile=/home/gsadmin/global-sentinel/.env`
- `openclaw-research.service` and `self-improvement-loop.service` both target the same legacy research entrypoint family and can double-run research workloads.
- `healthcheck.timer` can keep invoking a stale unit even after the main services have moved.

## Pre-Flight Inventory

Run these on the VM before disabling anything:

```bash
sudo systemctl list-unit-files \
  'global-sentinel*' \
  'openclaw*' \
  'healthcheck*' \
  'self-improvement-loop*'
```

```bash
for unit in \
  global-sentinel.service \
  global-sentinel-reconciler.service \
  global-sentinel-openclaw-ops.service \
  global-sentinel-openclaw-research.service \
  global-sentinel-dashboard.service \
  openclaw-ops.service \
  openclaw-research.service \
  healthcheck.service \
  healthcheck.timer \
  self-improvement-loop.service
do
  echo "=== $unit ==="
  sudo systemctl status "$unit" --no-pager || true
  sudo systemctl show "$unit" -p FragmentPath -p UnitFileState -p ActiveState -p SubState || true
done
```

Also check whether the old repo path still exists:

```bash
sudo ls -ld /home/gsadmin/global-sentinel || true
sudo test -f /home/gsadmin/global-sentinel/.env && echo legacy_env_present || echo legacy_env_missing
```

## Cleanup Sequence

1. Confirm the canonical units above are all `active` or intentionally `inactive` for a known reason.
2. Confirm `openclaw-ops.service` and `global-sentinel-openclaw-ops.service` are not both active.
3. Confirm `openclaw-research.service`, `self-improvement-loop.service`, and `global-sentinel-openclaw-research.service` are not overlapping.
4. If the canonical units are healthy, disable the legacy units:

```bash
sudo systemctl disable --now \
  openclaw-ops.service \
  openclaw-research.service \
  healthcheck.service \
  healthcheck.timer \
  self-improvement-loop.service
```

5. Mask the retired units to prevent accidental restarts from stale automation:

```bash
sudo systemctl mask \
  openclaw-ops.service \
  openclaw-research.service \
  healthcheck.service \
  healthcheck.timer \
  self-improvement-loop.service
```

6. Remove stale enablement symlinks only after the disabled state is confirmed:

```bash
sudo find /etc/systemd/system -maxdepth 2 -type l \
  \( -name 'openclaw-ops.service' \
  -o -name 'openclaw-research.service' \
  -o -name 'healthcheck.service' \
  -o -name 'healthcheck.timer' \
  -o -name 'self-improvement-loop.service' \) \
  -print
```

7. Run `sudo systemctl daemon-reload` and re-check unit state.

## Rollback

If any canonical unit fails after cleanup:

```bash
sudo systemctl unmask \
  openclaw-ops.service \
  openclaw-research.service \
  healthcheck.service \
  healthcheck.timer \
  self-improvement-loop.service
```

```bash
sudo systemctl enable --now \
  openclaw-ops.service \
  openclaw-research.service \
  healthcheck.timer \
  self-improvement-loop.service
```

Then inspect logs:

```bash
sudo journalctl -u global-sentinel-openclaw-ops.service -u openclaw-ops.service -n 200 --no-pager
sudo journalctl -u global-sentinel-openclaw-research.service -u openclaw-research.service -u self-improvement-loop.service -n 200 --no-pager
```

## Acceptance Criteria

- No active repo-shipped units remain that reference `/home/gsadmin/global-sentinel`.
- Only one ops orchestrator path is active.
- Only one research orchestrator path is active.
- `healthcheck.timer` is either migrated to the canonical path or intentionally retired.
- The canonical `global-sentinel-*` services remain healthy after `daemon-reload`.
