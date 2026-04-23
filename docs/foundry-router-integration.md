# Foundry Router Integration

## Purpose

This note audits the current model-call surfaces inside `global-sentinel`,
maps each one to the Foundry role it should become, and frames the migration
from GS-owned provider selection to `wrkflo-orchestrator -> Foundry router`
mediation.

The target boundary follows `docs/architecture-delta-gs-view.md:11-18` and
`docs/architecture-delta-gs-view.md:75-85`: Global Sentinel emits bounded
domain context, `wrkflo-orchestrator` chooses the routed role/profile, and
Foundry owns provider/deployment selection.

## Current Call-Site Audit

### App-path LLM callers

| Current caller | Current code | Current call shape | Current provider ownership | Target Foundry role |
| --- | --- | --- | --- | --- |
| Daily thesis generator | `scripts/ops/daily_thesis_generator.py:24-27`, `scripts/ops/daily_thesis_generator.py:56-75`, `scripts/ops/daily_thesis_generator.py:98-134` | Builds a morning prompt from `quantum_feed` files and posts directly to Azure `.../openai/deployments/{deployment}/chat/completions?api-version=...`. | Azure endpoint, key, deployment, and API version are loaded locally from `.env`. | `summarizer` for batch market-thesis synthesis. |
| Conversational market query | `scripts/ops/market_query.py:37-40`, `scripts/ops/market_query.py:75-130`, `scripts/ops/market_query.py:133-152` | Serializes all `quantum_feed/*.json`, posts directly to Azure chat completions, and stores the answer in `last_market_query.json`. | Azure endpoint, key, deployment, and API version are owned directly in the script. | `planner` for interactive decision support over GS context. |
| In-repo smart inference router | `src/monitoring/smart_inference_router.py:91-104`, `src/monitoring/smart_inference_router.py:147-241`, `src/monitoring/smart_inference_router.py:245-346` | Classifies prompt complexity and routes across OpenRouter Nemotron, Azure `gpt-5-mini`, Azure `gpt-4o`, and Anthropic `claude-opus-4-20250514`. | GS owns provider keys, base URLs, deployment names, fallback order, and routing logs. | Compatibility shim only; target mapping is `simple -> summarizer`, `moderate -> planner`, `complex -> critic`. |

### Infra and diagnostic provider ownership

| Surface | Current code | What it does now | Target posture |
| --- | --- | --- | --- |
| NemoClaw/OpenClaw staging wrapper | `scripts/ops/nemoclaw_stage_wrapper.sh:38-98`, `scripts/ops/nemoclaw_stage_wrapper.sh:100-144` | Reads Azure creds from `/etc/openclaw/openclaw.env`, creates provider `gs-azure-openai`, sets inference directly to Azure, and writes a local sandbox registry entry. | Should consume a routed Foundry profile or orchestrator-issued access token, not register Azure as a GS-owned/OpenClaw-owned provider. |
| NemoClaw blueprint | `config/nemoclaw/blueprint.yaml:23-52` | Declares direct GS Azure profiles (`gs-azure-mini`, `gs-azure-4o`) and policy allowances for Azure OpenAI. | Keep only as a staging shell if it points at orchestrator-managed profiles instead of raw Azure provider ownership. |
| Nemotron smoke script | `scripts/ops/test_nemotron.py:28-97` | Sends a direct OpenRouter `chat/completions` test request. | Keep only as a provider-level diagnostics script, not as an application-path model client. |

## Current vs Target State

### Current

- GS still owns direct Azure, OpenRouter, and Anthropic call construction.
- Routing policy is duplicated across scripts and the in-repo router.
- OpenClaw staging still registers its own provider and model selection.
- No application-path embeddings calls were found by grep, and no GS caller is
  currently using a dedicated Foundry executor role.

### Target

- GS sends a bounded request envelope to `wrkflo-orchestrator`.
- `wrkflo-orchestrator` selects the appropriate Foundry role/profile.
- Foundry owns provider selection, fallback order, deployment versions, and
  route tracing.
- GS records route metadata and model output, but no longer owns provider keys
  or deployment strings in app-path callers.

## Recommended GS Request Contract

Each migrated caller should emit a caller-neutral request envelope rather than a
provider-specific HTTP request. At minimum:

- `intent_type`
  - Examples: `daily_thesis`, `market_query`, `decision_support`.
- `target_role`
  - One of `summarizer`, `planner`, `critic`, `executor`, `embeddings`.
- `operating_context`
  - Include `mode`, `regime_shift_probability`, `kill_switch`,
    `manual_veto`, and execution sensitivity.
- `latency_class`
  - Example values: `interactive`, `batch`, `premium`.
- `trace_context`
  - `package_id`, `intent_id`, scorecard timestamp, or report path.

The returned envelope should include:

- `output`
- `route`
  - Selected provider/profile/deployment, fallback chain, latency, token usage
- `trace_id`
- `policy_annotations`
  - Any approval or safety metadata the caller must persist

## Role Mapping Summary

### App-path call count by target role

| Role | Current callers | Count | Notes |
| --- | --- | --- | --- |
| `summarizer` | `scripts/ops/daily_thesis_generator.py` | 1 | Batch synthesis over structured GS feeds. |
| `planner` | `scripts/ops/market_query.py` | 1 | Interactive, multi-source reasoning over current GS context. |
| `critic` | `src/monitoring/smart_inference_router.py` complex path | 1 shim | The current router treats complex prompts as premium reasoning, but this should be routed externally. |
| `executor` | none found in LLM call grep | 0 | GS execution remains in Python execution modules, not LLM tool execution. |
| `embeddings` | none found in grep | 0 | No current embeddings call sites in `src/` or `scripts/`. |

### Non-role provider-ownership surfaces

| Surface | Count |
| --- | --- |
| Provider-registration/staging surfaces | 2 |
| Provider diagnostics scripts | 1 |

## Migration Order

1. Introduce one GS-side Foundry client boundary.
   - `daily_thesis_generator.py` and `market_query.py` should stop constructing
     Azure URLs directly and should call a shared client instead.

2. Collapse the in-repo smart router into a shim.
   - Keep `smart_inference_router.py` only long enough to forward existing
     callers into the new client boundary.
   - Remove local provider keys and fallback policy from application logic.

3. Repoint app callers by role.
   - `daily_thesis_generator.py` -> `summarizer`
   - `market_query.py` -> `planner`
   - `smart_inference_router.py` complex compatibility path -> `critic`

4. Rework OpenClaw/NemoClaw staging.
   - `nemoclaw_stage_wrapper.sh` should stop creating `gs-azure-openai`.
   - `config/nemoclaw/blueprint.yaml` should point at orchestrator-managed or
     Foundry-managed profiles rather than direct Azure credentials.

5. Leave provider smoke tests out of the app path.
   - `test_nemotron.py` can remain as an ops-only diagnostic, but it should not
     be treated as a supported runtime integration path.

## Verification Commands

Use these commands after migration work:

```bash
cd /home/moses/projects/global-sentinel

rg -n "chat/completions|api\\.anthropic\\.com|openrouter\\.ai|openshell provider create|openshell inference set" \
  src scripts config

rg -n "target_role|trace_id|route|/v1/tasks|/v1/runs" src scripts
```

Expected end state:

- The first grep should no longer show app-path scripts constructing provider
  URLs or staging scripts creating providers as the primary route owner.
- The second grep should show GS callers emitting orchestrator/Foundry request
  envelopes and recording returned route metadata.
