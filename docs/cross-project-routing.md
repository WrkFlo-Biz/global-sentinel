# Cross-Project Routing

## Purpose

This note is the quick router for the projects currently visible from the VM.
Use it to decide which repo to open before making changes or answering project
questions.

## Routing Order

When a prompt could fit more than one repo, route in this order:

1. Use the repo that owns the live runtime behavior being changed.
2. If the task is primarily audit, validation, call-flow, or operational
   documentation, use the docs/ops repo instead of the runtime repo.
3. If the task is about local VM access, launchers, Mac control, or model
   profiles, use `dev-workspace`.
4. Use `global-sentinel-azure-quantum` only for a deliberate future
   extraction, not for current quantum implementation work.

## Repo Purpose

### `global-sentinel`

Primary application repo for geopolitical risk intelligence, supervised
execution orchestration, policy controls, dashboard logic, and the active
quantum research lane.

Open this repo for:

- ingest, scoring, orchestration, and execution logic
- policy/risk controls and shadow-mode behavior
- dashboard and reporting work
- quantum lane validation, research artifacts, and research guardrails
- systemd, VM runtime, and Global Sentinel operational runbooks

### `openclaw-prod`

Production deployment repo for OpenClaw on Azure Container Apps. This is the
right place for OpenClaw gateway/runtime deployment, container packaging,
dashboard hosting, and shared bot operations inside the OpenClaw environment.

Open this repo for:

- OpenClaw container/runtime behavior
- Azure Container Apps deployment for OpenClaw
- OpenClaw gateway, canvas, and shared bot operations
- OpenClaw-specific infra or automation

### `wrkflo-voice-agents-ops`

Documentation and audit repo for Wrk.Flo voice-agent operations. This is
primarily an evidence, runbook, and validation surface rather than the main
runtime implementation.

Open this repo for:

- voice-agent audit findings and validation packs
- telephony/integration runbooks
- architecture docs and operational evidence
- security/access templates and testing notes

### `global-sentinel-azure-quantum`

Currently a dormant extraction target only. It is initialized as a repo but has
no active implementation, no docs, and no working code at this time.

Open this repo only when:

- intentionally starting a standalone extraction of the quantum lane
- creating a new independent repo structure for quantum-specific work

Do not treat it as the source of truth for current quantum behavior. The active
implementation still lives in `global-sentinel`.

## Which Repo For Common Task Types

- Trading, risk, execution, signal flow, dashboard, or policy question:
  `global-sentinel`
- Quantum lane behavior, validation, or Azure quantum research pipeline:
  `global-sentinel`
- OpenClaw deployment, gateway, ACA runtime, or shared bot dashboard:
  `openclaw-prod`
- Voice-agent audits, docs, incident notes, or integration validation:
  `wrkflo-voice-agents-ops`
- New standalone quantum-repo scaffolding only:
  `global-sentinel-azure-quantum`

## Prompt Triage

- `broker`, `risk`, `execution`, `orders`, `policy`, `signals`, `portfolio`,
  `dashboard`, `quantum lane`:
  `global-sentinel`
- `voice`, `calls`, `Twilio`, `ElevenLabs`, `telephony`, `audit`,
  `validation pack`, `runbook`:
  `wrkflo-voice-agents-ops`
- `OpenClaw`, `gateway`, `runtime`, `deploy`, `ACA`, `dashboard`,
  `management board`, `shared ops`:
  `openclaw-prod`
- `dev VM`, `launcher`, `Mac bridge`, `9222`, `9223`, `Foundry profiles`,
  `Codex profiles`, `VM->Mac SSH`:
  `dev-workspace`

`dev-workspace` is an external helper workspace, not a subdirectory of this
repo. Route there only for workstation/session tooling tasks.

## Ambiguous Cases

- If a prompt says `dashboard`, route by the system named in the prompt:
  - `portfolio dashboard`, `trading dashboard`, `regime dashboard`, or
    `Global Sentinel dashboard` -> `global-sentinel`
  - `OpenClaw dashboard`, `management board`, `gateway UI`, or canvas/UI served
    by OpenClaw -> `openclaw-prod`
- If a prompt mixes trading logic with infrastructure, start in
  `global-sentinel` unless it explicitly names OpenClaw runtime/deploy.
- If a prompt is about documentation, audits, call flows, or telephony
  reliability, route to `wrkflo-voice-agents-ops` even when it mentions
  production incidents.
- If a prompt says `VM runtime`, split by ownership:
  - app/service runtime, monitors, healthchecks, systemd behavior, or data flow
    for Global Sentinel -> `global-sentinel`
  - launcher defaults, local session access, Mac bridge, `9222`/`9223`,
    `VM->Mac SSH`, or model/profile bootstrapping -> `dev-workspace`
- If a prompt describes a voice incident, split by task shape:
  - call flow, Twilio/ElevenLabs behavior, webhook notes, audit evidence,
    validation packs, or incident/runbook documentation ->
    `wrkflo-voice-agents-ops`
  - OpenClaw gateway/runtime deployment or ACA service behavior that happens to
    affect a voice workflow -> `openclaw-prod`
- Do not route current quantum implementation work to
  `global-sentinel-azure-quantum`; use `global-sentinel` unless the task is to
  create the extraction itself.

## Example Prompts

- "The dashboard is broken" -> clarify whether this means the Global Sentinel
  dashboard or the OpenClaw management dashboard before routing
- "The VM is broken" -> clarify whether this means Global Sentinel service/app
  runtime (`global-sentinel`) or launcher/access/Mac bridge tooling
  (`dev-workspace`)
- "There is a voice production incident" -> clarify whether this is call
  flow/audit/runbook work (`wrkflo-voice-agents-ops`) or an OpenClaw
  gateway/runtime outage (`openclaw-prod`)
- "Why was this broker order blocked?" -> `global-sentinel`
- "The portfolio dashboard widget is wrong" -> `global-sentinel`
- "The OpenClaw management dashboard is down" -> `openclaw-prod`
- "Why are Twilio calls failing or notes not posting?" ->
  `wrkflo-voice-agents-ops`
- "Calls are failing because the OpenClaw ACA gateway/webhook path is down" ->
  `openclaw-prod`
- "The GS systemd service is unhealthy on the VM" -> `global-sentinel`
- "New sessions do not inherit the right launcher access or Mac bridge
  settings" -> `dev-workspace`
- "Redeploy the OpenClaw gateway and fix the management dashboard" ->
  `openclaw-prod`
- "Make Codex sessions start with the right VM access and Mac controls" ->
  `dev-workspace`

## Default Rule

If the task touches live code or current system behavior, start in
`global-sentinel` unless the request is clearly OpenClaw-specific or
voice-agent-ops-specific. If a prompt spans multiple repos, start in the repo
that owns the behavior being changed, not the repo that merely documents it.
