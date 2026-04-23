from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
SMART_ROUTER_MODULE = "src.monitoring.smart_inference_router"
SMART_ROUTER_PATH = (SRC_ROOT / "monitoring" / "smart_inference_router.py").resolve()


def _deprecated_router_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == SMART_ROUTER_MODULE:
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module == SMART_ROUTER_MODULE:
                imported = ", ".join(alias.name for alias in node.names)
                violations.append(f"from {node.module} import {imported}")
            elif node.module == "src.monitoring":
                imported = [alias.name for alias in node.names if alias.name == "smart_inference_router"]
                if imported:
                    violations.append("from src.monitoring import smart_inference_router")

    return violations


def test_production_modules_do_not_import_deprecated_smart_inference_router() -> None:
    offenders: list[str] = []

    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path.resolve() == SMART_ROUTER_PATH:
            continue

        violations = _deprecated_router_imports(path)
        if violations:
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {', '.join(violations)}")

    assert not offenders, (
        "Production inference callers must use src.inference.foundry_client directly.\n"
        + "\n".join(offenders)
    )
