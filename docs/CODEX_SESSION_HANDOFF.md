# Codex GS Session Handoff

## Session Identity
- **Model**: gpt-5.4 high (Codex v0.122.0)
- **Working directory**: `~/projects/global-sentinel` on dev-workspace-vm
- **Sandbox**: Codex sandbox with .git mounted read-only (git commits require unsandboxed approval)
- **Why it stopped**: Context window full, compaction endpoint returning "Error running remote compact task: high demand" — not an API key issue

## Completed Work

### Step 1: `docs/cross-project-routing.md` — committed `8074903`
- Comprehensive routing guide for all 5 Wrk-Flo repos
- Sections: Routing Order, Repo Purpose, Prompt Triage (keyword -> repo mapping), Ambiguous Cases, Example Prompts, Default Rule
- Covers dashboard ownership split (GS vs OpenClaw), VM runtime vs launcher/Mac bridge split, voice incident routing (wrkflo-voice-agents-ops vs openclaw-prod)
- Also added a one-line discoverability pointer in `docs/SYSTEM_OVERVIEW_FOR_GPT.md` (line 16)

### Step 2: `docs/architecture-delta-gs-view.md` — committed `e7cd6ec`
- One-pager: GS perspective on the new architecture chain
- Chain: user -> Termius -> VM -> wrkflo-orchestrator -> Foundry router -> tools
- **GS sends to orchestrator**: regime_shift_probability, operating mode (NORMAL/ELEVATED/CRISIS/MANUAL_REVIEW), crisis flags, shadow_execution_eligible, veto/kill-switch state, policy trace, trade/research intent metadata
- **GS consumes from orchestrator**: policy verdicts, approval status, routed model responses, brokered tool access, workspace execution status
- Design consequence: GS goes from "decides and routes everything" to "decides market/policy facts and delegates generic orchestration"

### Shared Codex Memory: `~/.codex/memories/workspace-project-map.md`
- Inventory of all 5 project roots with purpose, key docs, and main code areas
- Division of labor between terminal sessions documented
- Safe non-overlapping work areas flagged

## Incomplete Work (Steps 3-5)

### Step 3: `docs/openclaw-demotion.md` — NOT STARTED (was mid-research when context died)
**Task**: Read-only audit of every OpenClaw/Telegram coupling point in GS code.
- Grep targets: `openclaw`, `telegram`, `mo2darkbot`, `mo2drkbot`, `openclaw_data_feed` under `src/` and `scripts/`
- For each hit: list file:line, what GS does (sending, receiving, or command handler), and the orchestrator-API call that should replace it post-migration
- Format as scannable table or bullet list. No code edits.

**Research already done** (these files were read but no doc was written):
- `src/integrations/openclaw_data_feed.py`
- `scripts/ops/start_telegram_bots.py`
- `src/integrations/openclaw_research_bridge.py`
- `src/integrations/unified_search_bridge.py`
- `src/integrations/telegram_bot_manager.py`
- `src/integrations/telegram_command_handler.py`
- `src/integrations/openclaw_role_registry.py`
- `src/integrations/openclaw_role_briefing.py`

### Step 4: `docs/foundry-router-integration.md` — NOT STARTED
**Task**: Grep `src/` and `scripts/` for: `AZURE_OPENAI`, `openai.`, `ChatCompletion`, `embedding`, `AsyncOpenAI`.
- For each hit: list file:line, current call shape, and which FoundryRouter role it maps to (planner / critic / executor / summarizer / embeddings)
- End with a "call-count by role" summary

### Step 5: `docs/permission-tiers.md` — NOT STARTED
**Task**: Classify current GS actions into Tier 0 (read) / Tier 1 (safe dev) / Tier 2 (guarded).
- Walk: `src/execution/`, `src/options/`, `src/strategies/`, `src/reports/`, and anywhere GS writes to Telegram or pushes commits
- Flag every Tier-2 action that currently runs without approval
- Recommend where the orchestrator approval gate should sit for each

## After Steps 3-5: Broader Roadmap

A full handoff was delivered from the Claude orchestrator session. Durable brief lives at:
`Wrk-Flo/wrkflo-orchestrator:docs/HANDOFF.md` (commit `ebee84a`)

**Read that file first** — it has current state, in-flight work, full roadmap, authority scope, invariants, and verification commands.

Suggested first post-batch task (Phase 6): Stand up a minimal HTTP API on `wrkflo-orchestrator` exposing `POST /v1/tasks` + `GET /v1/runs/{id}`, wired to the existing Orchestrator core. Keep it tiny — stdlib `http.server` is fine for v0.

## Repo State at Time of Failure

| Repo | HEAD | Status |
|------|------|--------|
| `global-sentinel` (VM) | `e7cd6ec` docs: add gs architecture delta view | Clean except untracked `.codex/` |
| `wrkflo-orchestrator` (VM) | `ebee84a` (includes HANDOFF.md) | Clean, just pulled |
| `dev-workspace` (VM) | `38226d4` | Owned by OTHER session — do not touch |

## Invariants (Carry Forward)
- Append-only state; contracts validate on every handoff
- Tier 2 never auto-executes without approval hook
- FoundryRouter key never logged
- `.codex` directories never touched
- Surface a one-line note to user before: secret rotation, NSG changes, prod deploys, Telegram sends
- Stay inside `~/projects/global-sentinel` for steps 3-5
- Do not edit `~/dev-workspace` or sibling repos for this batch
- Commit after each doc with a scoped message, print `git status --short` after each

## Quick Start for New Session
```bash
# 1. Confirm repo state
cd ~/projects/global-sentinel
git log --oneline -3
# expect: e7cd6ec, 8074903, ...

git status --short
# expect: ?? .codex  (only)

# 2. Read the orchestrator handoff brief
cat ~/projects/wrkflo-orchestrator/docs/HANDOFF.md

# 3. Start step 3: openclaw demotion audit
grep -rn 'openclaw\|telegram\|mo2darkbot\|mo2drkbot\|openclaw_data_feed' src/ scripts/ --include='*.py'
# Then write docs/openclaw-demotion.md from findings
```
