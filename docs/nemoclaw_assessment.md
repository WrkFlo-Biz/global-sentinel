# NemoClaw Assessment For Global Sentinel

## Recommendation

Use NVIDIA NemoClaw as a staged side-by-side security wrapper, not as an immediate
replacement for the current Azure VM OpenClaw runtime.

## What It Improves

- Deny-by-default egress policy around OpenClaw bot traffic.
- Host-side provider credentials instead of embedding raw API keys in agent state.
- A separate OpenShell gateway and sandbox lifecycle for staging and rollback.
- Cleaner path to isolating one assistant or role bot at a time.

## What It Does Not Meaningfully Improve On This VM

- No GPU acceleration. The Azure VM has no NVIDIA device, so NIM or local Nemotron
  acceleration is not a practical benefit here.
- No better container uptime than the current Docker-based `openclaw-gateway.service`.
- No automatic preservation of the current multi-model fallback chain. The shipped
  NemoClaw blueprint centers on one managed inference route at a time.

## Current Recommendation

Keep the current runtime as production on `127.0.0.1:18789`.

Stage NemoClaw in parallel with:

- OpenShell gateway: `gs-nemoclaw` on port `18080`
- OpenClaw sandbox: `gs-wrapper`
- Forwarded control port: `127.0.0.1:18790`

This adds policy and isolation without risking the existing bot fleet.

## Current Limitation

NemoClaw's stock runner does not fully migrate the current Azure OpenClaw runtime
into the sandbox. It can create the sandbox, set the inference route, and apply
policy, but bot-by-bot cutover still needs deliberate migration work.

In practice, the shipped `runner.js apply` path is also not strong enough to be
the only automation layer here. The staged rollout script uses direct OpenShell
commands for sandbox, provider, inference, and policy so the result is explicit
and debuggable on this VM.

That makes staged adoption the defensible path:

1. Start a side-by-side sandbox.
2. Validate Telegram, inference, and selected tools through policy.
3. Migrate one bot profile at a time if the wrapper proves worth the operational
   complexity.
