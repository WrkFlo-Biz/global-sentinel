# Foundry Router Integration

## Purpose

This note audits the current LLM and Azure call surfaces inside
`global-sentinel`, maps each runtime caller to the target
`FoundryRouter` role vocabulary from
`/home/moses/projects/wrkflo-orchestrator/docs/architecture-delta.md:26`,
and identifies the remaining provider-owned escape hatches that still live in
GS code.

Target vocabulary from orchestrator docs:

- `planner`
- `critic`
- `executor`
- `summarizer`
- `embeddings`
- `realtime`

This doc covers the synchronous GS inference boundary (`/v1/inference` via
`src/inference/foundry_client.py`). It is adjacent to, but distinct from, the
async task/run surfaces used in the OpenClaw demotion plan
(`POST /v1/tasks`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/history`).

## Audit Scope

Audit scope was limited to `src/` and `scripts/`, with emphasis on direct hits
for:

- `AZURE_OPENAI`
- `openai.`, `OpenAI(`, `AsyncOpenAI(`, `ChatCompletion`
- `anthropic`
- `embedding`
- direct Azure OpenAI / Foundry HTTP endpoints
- shared client wrappers that front those calls

Notable grep result: there are no current `openai` SDK call sites in
`src/` or `scripts/`, no `ChatCompletion` hits, no `responses.create` hits,
and no app-path embeddings or realtime callers. The active GS path is a shared
wrapper in `src/inference/foundry_client.py`, plus one remaining direct
Anthropic caller and a couple of ops-only provider bootstrap/diagnostic
surfaces.

## Runtime Callers Mapped To Foundry Roles

| Surface | File:line | Current call shape | Intended FoundryRouter role |
| --- | --- | --- | --- |
| Shared GS inference boundary | `src/inference/foundry_client.py:48-90`, `src/inference/foundry_client.py:102-118` | `send_request(...)` builds a bounded envelope with `intent_type`, `target_role`, `operating_context`, `latency_class`, `trace_context`, and `messages`, then `httpx.post()`s to `ORCHESTRATOR_URL` (`http://localhost:8100/v1/inference` by default). | Shared boundary for `planner`, `critic`, `executor`, `summarizer`, and `embeddings`. This is the intended GS entrypoint into orchestrator-managed Foundry routing. |
| Azure fallback inside shared boundary | `src/inference/foundry_client.py:121-175` | On `httpx.RequestError`, non-`embeddings` requests fall back to direct Azure `POST {AZURE_OPENAI_ENDPOINT}/openai/deployments/{AZURE_OPENAI_DEPLOYMENT}/chat/completions?api-version={AZURE_OPENAI_API_VERSION}` with `api-key`. | Same role as the original request, but this is an escape hatch, not the target steady state. `realtime` is not supported here, and `embeddings` explicitly has no Azure fallback. |
| Daily thesis generator | `scripts/ops/daily_thesis_generator.py:94-116`, `scripts/ops/daily_thesis_generator.py:179-184` | Calls `send_request(intent_type="daily_thesis", target_role="summarizer", latency_class="batch", ...)` with a system prompt plus a synthesized market-thesis user prompt. | `summarizer` |
| Conversational market query | `scripts/ops/market_query.py:100-138`, `scripts/ops/market_query.py:155-160` | Calls `send_request(intent_type="market_query", target_role="planner", latency_class="interactive", ...)` with a large serialized `quantum_feed` context and the user question. | `planner` |
| Legacy smart router shim | `src/monitoring/smart_inference_router.py:55-69`, `src/monitoring/smart_inference_router.py:244-348` | Classifies prompt complexity, then forwards through `foundry_client.send_request(...)` with `simple -> summarizer`, `moderate -> planner`, `complex -> critic`. The module declares itself deprecated and says there are no in-tree runtime callers left. | Compatibility shim spanning `summarizer`, `planner`, and `critic` |
| Telegram free-form LLM reply | `src/monitoring/telegram_command_handler.py:258-289` | Instantiates `anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))` and calls `client.messages.create(model="claude-opus-4-6", max_tokens=800, system=..., messages=[...])` directly. | `planner` is the closest semantic fit for interactive operator Q&A over live GS context. It should move behind the shared GS boundary so orchestrator owns provider choice. |

## Provider-Owned Ops Surfaces Still In Tree

These hits are relevant because they still hard-code provider details in
`src/` or `scripts/`, but they are not clean application-role callers.

| Surface | File:line | Current call shape | Target posture |
| --- | --- | --- | --- |
| NemoClaw / OpenClaw staging wrapper | `scripts/ops/nemoclaw_stage_wrapper.sh:38-104`, `scripts/ops/nemoclaw_stage_wrapper.sh:136-144` | Reads `AZURE_OPENAI_*` from `/etc/openclaw/openclaw.env`, exports `OPENAI_API_KEY`, creates `openshell` provider `gs-azure-openai`, and runs `openshell inference set --provider 'gs-azure-openai' --model ...`. | Remove GS-owned provider registration from this script. It should consume an orchestrator-managed route or profile instead of deciding Azure/OpenAI wiring locally. |
| Nemotron smoke script | `scripts/ops/test_nemotron.py:28-30`, `scripts/ops/test_nemotron.py:56-77`, `scripts/ops/test_nemotron.py:85-96` | Builds `https://openrouter.ai/api/v1/chat/completions` and posts a one-shot test prompt with `Authorization: Bearer $OPENROUTER_API_KEY`. | Keep only as provider diagnostics. Do not treat this as a supported runtime role path. |

## What The Grep Did Not Find

- No `openai` Python SDK clients in `src/` or `scripts/`
- No `ChatCompletion` or `responses.create` usage
- No current embeddings API callers
- No current realtime API callers
- No in-tree runtime caller selecting `executor`
- No in-tree runtime caller selecting `embeddings`
- No GS-side enum or caller for `realtime`; that role exists only in the
  orchestrator vocabulary today

## Current State vs Target Boundary

### Current

- GS app-path callers mostly route through `src/inference/foundry_client.py`.
- The shared wrapper already emits the correct orchestrator envelope shape.
- The wrapper still owns a direct Azure fallback for non-embeddings chat calls.
- One runtime path still bypasses the wrapper entirely:
  `src/monitoring/telegram_command_handler.py` uses Anthropic directly.
- Two ops-only surfaces still own provider wiring directly:
  `scripts/ops/nemoclaw_stage_wrapper.sh` and `scripts/ops/test_nemotron.py`.

### Target

- All GS runtime LLM traffic should enter through one GS-side boundary:
  `src.inference.foundry_client.send_request(...)`.
- Orchestrator should be the only layer that resolves the Foundry role to an
  actual deployment/provider choice.
- GS should stop constructing direct provider URLs except, if temporarily
  necessary, in tightly controlled diagnostics outside the main app path.
- The direct Anthropic Telegram path should collapse into the same boundary and
  let orchestrator decide whether `planner` lands on Anthropic, GPT, or another
  Foundry-backed deployment.

## Call Count By Role

Counts below are runtime call paths in `src/` and `scripts/`, not helper enums
or documentation mentions.

| Role | Current call paths | Count | Notes |
| --- | --- | --- | --- |
| `planner` | `scripts/ops/market_query.py`; `src/monitoring/telegram_command_handler.py`; `src/monitoring/smart_inference_router.py` moderate branch | 3 | One direct vendor bypass remains in Telegram; the other two already point at the GS wrapper. |
| `critic` | `src/monitoring/smart_inference_router.py` complex branch | 1 | Compatibility-only path; the module says no in-tree callers remain. |
| `executor` | none found | 0 | The wrapper enum supports it, but no caller selects it today. |
| `summarizer` | `scripts/ops/daily_thesis_generator.py`; `src/monitoring/smart_inference_router.py` simple branch | 2 | Daily thesis is the only active app-path caller with an explicit static role. |
| `embeddings` | none found | 0 | The wrapper enum supports it, but no caller selects it and Azure fallback is intentionally unimplemented. |
| `realtime` | none found | 0 | Present in orchestrator vocabulary only; no GS caller or wrapper support yet. |

## Practical Migration Order

1. Move `src/monitoring/telegram_command_handler.py` behind
   `src.inference.foundry_client.send_request(...)` and map it to `planner`.
2. Keep `scripts/ops/daily_thesis_generator.py` on `summarizer` and
   `scripts/ops/market_query.py` on `planner`; they are already close to the
   target shape.
3. Leave `src/monitoring/smart_inference_router.py` as a compatibility shim
   only, then remove it once external callers are migrated.
4. Remove Azure/OpenAI provider registration from
   `scripts/ops/nemoclaw_stage_wrapper.sh`.
5. Keep `scripts/ops/test_nemotron.py` as diagnostics-only, outside the
   supported runtime integration path.
