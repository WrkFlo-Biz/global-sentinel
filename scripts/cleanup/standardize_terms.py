#!/usr/bin/env python3
"""
Fail CI if legacy terminology appears.
Standard: submitted_orders -> bound_order_attempts
"""

from pathlib import Path

FORBIDDEN = [
    "submitted_orders",
    "submitted_order_count",
    "submitted_orders_count",
    "Submitted Orders",
]

ALLOWLIST_PATHS = {
    "scripts/cleanup/standardize_terms.py",  # this file references the tokens
    "logs/",  # historical log data
    "reports/",  # generated report artifacts
    ".github/workflows/",  # step display names may reference old terms
}


def main() -> int:
    root = Path(".").resolve()
    bad = []

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".py", ".md", ".yml", ".yaml", ".json"}:
            continue
        rel = str(p.relative_to(root))

        # Skip allowlisted paths
        skip = False
        for allow in ALLOWLIST_PATHS:
            if rel == allow or rel.startswith(allow):
                skip = True
                break
        if skip:
            continue

        # Skip test output artifacts (generated data)
        if "/out/" in rel:
            continue

        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for token in FORBIDDEN:
            if token in txt:
                bad.append((rel, token))

    if bad:
        print("Terminology check failed. Replace legacy fields:")
        for rel, token in bad[:200]:
            print(f" - {rel}: contains '{token}'")
        print("\nRequired replacements:")
        print(" - submitted_orders -> bound_order_attempts")
        print(" - submitted_orders_count -> bound_order_attempt_count")
        return 1

    print("Terminology check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
