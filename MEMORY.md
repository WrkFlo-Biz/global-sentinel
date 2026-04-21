## Global Sentinel Notes

- Production repo on the Azure VM is the source of truth for live trading-adjacent ops checks.
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

- Prefer VM verification over assumptions.
- Keep plaintext secrets out of repo memory files and docs.
- Treat the VM worktree as potentially dirty; avoid broad git operations.
