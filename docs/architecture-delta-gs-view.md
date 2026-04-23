# Architecture Delta: Global Sentinel View

## Purpose

This note describes the current target architecture from the Global Sentinel
perspective after introducing `wrkflo-orchestrator` as the mediation layer
between the VM workspace and model/tool execution.

## End-to-End Chain

```text
user
  -> Termius
  -> VM
  -> wrkflo-orchestrator
  -> Foundry router
  -> tools / providers / side-effect surfaces
```

Global Sentinel sits on the VM as the domain system that computes market state,
policy context, and execution eligibility. It should no longer be treated as
the top-level tool router for all downstream model/tool access.

## Where Global Sentinel Sits

Global Sentinel owns:

- market and geopolitical ingestion
- regime scoring and crisis-mode determination
- risk, policy, and execution guardrails
- trade idea packaging and execution eligibility decisions
- reporting and manual-review context

Global Sentinel does **not** need to own:

- model routing across Foundry deployments
- workspace/session brokerage
- generic approval transport
- direct tool multiplexing that is not GS-domain-specific

Those concerns move outward to `wrkflo-orchestrator`.

## What Global Sentinel Should Send To The Orchestrator

When GS requests orchestration or approval-aware execution, it should send a
bounded decision context rather than raw shell/session control. The minimum
GS-side payload should include:

- `regime_shift_probability`
- current operating mode (`NORMAL`, `ELEVATED`, `CRISIS`, `MANUAL_REVIEW`)
- crisis-related flags derived from GS mode and controls
- `shadow_execution_eligible` / execution-eligibility state
- manual veto / kill-switch state
- policy trace or policy decision summary when available
- trade or research intent metadata needed for downstream approval decisions

In practice, GS already computes many of these fields in scorecards, policy
outputs, and reporting envelopes; the orchestrator should consume the summary,
not reconstruct GS logic on its own.

## What Global Sentinel Should Consume From The Orchestrator

GS should treat the orchestrator as the external control plane for:

- policy verdict transport
- approval status / approval decisions
- routed model responses from the Foundry router
- brokered access to tools that sit outside GS proper
- workspace-level execution status for guarded actions

The important boundary is that GS remains the source of truth for domain risk
facts, while the orchestrator becomes the source of truth for routed execution
and approval mediation.

## Expected GS-Orchestrator Contract

From the GS side, the integration target is:

1. GS computes regime, risk, and policy context.
2. GS emits a compact request to `wrkflo-orchestrator`.
3. The orchestrator chooses the appropriate Foundry-router role or approval
   path.
4. The orchestrator returns a policy verdict, approval result, or routed model
   output.
5. GS records the returned verdict/output in its own audit and reporting path.

## Design Consequence

This changes GS from "system that both decides and routes everything" to
"system that decides market/policy facts and delegates generic orchestration."
That keeps GS focused on regime intelligence, risk controls, and execution
guardrails while allowing the orchestrator to own Foundry routing, workspace
brokering, and approval transport.
