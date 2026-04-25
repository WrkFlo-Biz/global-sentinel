from __future__ import annotations


def orchestrator_approval_command(
    kind: str,
    target: str,
    *,
    include_reason_placeholder: bool = False,
) -> str:
    command = f"wrkflo-orchestrator approve --kind {kind} --target {target}"
    if include_reason_placeholder:
        command += ' --reason "<reason>"'
    return command


def orchestrator_approval_guidance(
    kind: str,
    target: str,
    *,
    include_reason_placeholder: bool = False,
) -> str:
    return (
        "This command now requires orchestrator approval. Use: "
        + orchestrator_approval_command(
            kind,
            target,
            include_reason_placeholder=include_reason_placeholder,
        )
    )
