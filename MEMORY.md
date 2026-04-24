## Global Sentinel Notes

- Host boundary:
- `dev-workspace-vm` is the development machine and current Codex workspace host.
- `openclaw-gateway-vm` (`openclaw@20.124.180.8`) is a separate, actively operated live-ops machine.
- Project boundary:
- `/home/moses/projects/global-sentinel`, `/home/moses/projects/wrkflo-orchestrator`, and `/home/moses/dev-workspace` are different projects/repos with different ownership, runtime role, and git state.
- Live trading-adjacent ops checks for Global Sentinel should be verified against `/opt/global-sentinel` on `openclaw-gateway-vm`, not inferred from the local `global-sentinel` checkout on `dev-workspace-vm`.
- Recent verified commits:
  - `7888888`: IBM quantum upgrades plus Nemotron fallback scaffolding.
  - `d8828c8`: FRED ops dashboard, trade approval, router hardening, and follow-up fixes.
- Core scheduled services currently verified:
  - `gs-quantum-risk.timer`
  - `gs-quantum-kernel.timer`
  - `gs-quantum-pricing.timer`
  - `gs-fred-calendar.timer`
  - `gs-health-dashboard.timer`

## Operating Rules

- Confirm `hostname`, `whoami`, and repo path before any git, tmux, or service action.
- Do not conflate `dev-workspace-vm` with `openclaw-gateway-vm`; they have different repo state and risk.
- Do not conflate `global-sentinel`, `wrkflo-orchestrator`, and `dev-workspace`; they are separate projects even when they appear in the same terminal session or VM.
- Prefer gateway VM verification over assumptions for live ops state.
- Keep plaintext secrets out of repo memory files and docs.
- Treat both VM worktrees as potentially dirty; avoid broad git operations.
