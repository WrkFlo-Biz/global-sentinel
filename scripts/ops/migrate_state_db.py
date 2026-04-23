#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.core.openclaw_state_db import OpenClawStateDB, default_state_db_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or upgrade OpenClaw state.db schema.")
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repo root used to resolve the default state.db path.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Explicit SQLite database path. Defaults to <repo-root>/state.db or OPENCLAW_STATE_DB_PATH.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    db_path = Path(args.db_path).expanduser() if args.db_path else default_state_db_path(repo_root)
    state_db = OpenClawStateDB(db_path)
    print(json.dumps(state_db.schema_snapshot(), indent=2))


if __name__ == "__main__":
    main()
