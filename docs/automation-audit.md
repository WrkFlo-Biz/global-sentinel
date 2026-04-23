# Automation Audit

Date: 2026-04-23 UTC
Repo: `/home/moses/projects/global-sentinel`

## Scope

- Searched the full repo for the deprecated unattended repo-mutation surface and
  removed the remaining in-repo references.
- Audited every file in the repo root for unattended automation risk.
- Focused on root entrypoints and automation-adjacent config, not the full
  `scripts/systemd/` tree.

## Deprecated Repo Mutation Surface

- Repo-wide search for the deprecated auto-commit helper returned only:
  - documentation references
  - the compatibility stub under `scripts/ops/`
- No runtime imports, cron entries, GitHub workflow hooks, or systemd units in
  this repo referenced that helper.
- Action taken:
  - removed the deprecated stub from `scripts/ops/`
  - removed the remaining documentation references

## Root File Audit

Reviewed root files:

- `.codex`
- `.env.example`
- `.gitignore`
- `AGENTS.md`
- `CLAUDE.md`
- `HEARTBEAT.md`
- `IDENTITY.md`
- `MARKET_RESEARCH_SPEC.md`
- `MEMORY.md`
- `MONDAY_CHECKLIST.md`
- `Makefile`
- `README.md`
- `SOUL.md`
- `TOOLS.md`
- `USER.md`
- `mcp.json`
- `requirements.txt`
- `server.py`

## Findings

| File | Category | Unattended automation risk | Notes |
| --- | --- | --- | --- |
| `server.py` | Service entrypoint | Medium | FastAPI dashboard/API entrypoint. Not self-scheduling, but if launched under systemd or another process manager it can expose control and execution-adjacent APIs. Requires external invocation to run. |
| `mcp.json` | Automation-adjacent config | Medium | Declares MCP servers that can launch local Python bridges and a paper-trading fetch server on demand. Not unattended by itself, but it expands the callable automation surface when loaded by an MCP host. |
| `Makefile` | Operator task runner | Low | Wraps test and research/report generation commands. No self-scheduling, no repo mutation, and no direct approval bypass in the root targets inspected. |
| `.env.example` | Config template | None | Static environment template. No execution behavior. |
| `.gitignore` | VCS config | None | No execution behavior. |
| `requirements.txt` | Dependency manifest | None | No execution behavior. |
| `.codex` | Sentinel file | None | Read-only marker file. No execution behavior. |
| `AGENTS.md` | Documentation | None | Instructional only. |
| `CLAUDE.md` | Documentation | None | Instructional only. |
| `HEARTBEAT.md` | Documentation | None | Instructional only. |
| `IDENTITY.md` | Documentation | None | Instructional only. |
| `MARKET_RESEARCH_SPEC.md` | Documentation | None | Instructional only. |
| `MEMORY.md` | Documentation | None | Instructional only. |
| `MONDAY_CHECKLIST.md` | Documentation | None | Instructional only. |
| `README.md` | Documentation | None | Instructional only. |
| `SOUL.md` | Documentation | None | Instructional only. |
| `TOOLS.md` | Documentation | None | Instructional only. |
| `USER.md` | Documentation | None | Instructional only. |

## Conclusions

- No root-level file besides `server.py`, `mcp.json`, and `Makefile` expands the
  automation surface.
- None of the root files are self-scheduling unattended mutation jobs.
- The main root-level automation concern is not autonomous execution from the
  root itself, but externally launched services or MCP hosts that can start
  `server.py` or the MCP commands declared in `mcp.json`.
- Unattended repo mutation has been removed from the repo's live code and
  documentation surfaces.
