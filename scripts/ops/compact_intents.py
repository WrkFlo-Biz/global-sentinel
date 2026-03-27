#!/usr/bin/env python3
"""Compact order_intents.jsonl - keep only latest state per intent_id."""
import json
from pathlib import Path
from datetime import datetime

path = Path("/opt/global-sentinel/logs/execution/order_intents.jsonl")
if not path.exists():
    print("No file to compact")
    exit(0)

size_before = path.stat().st_size / 1024 / 1024
latest = {}
with open(path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            iid = obj.get("intent_id", obj.get("id", ""))
            if iid:
                latest[iid] = line
        except json.JSONDecodeError:
            continue

backup = path.with_suffix(f".jsonl.bak.{datetime.now().strftime(chr(37)+chr(89)+chr(37)+chr(109)+chr(37)+chr(100))}")
path.rename(backup)
with open(path, "w") as f:
    for line in latest.values():
        f.write(line + "\n")

size_after = path.stat().st_size / 1024 / 1024
print(f"Compacted: {size_before:.1f}MB -> {size_after:.1f}MB ({len(latest)} intents)")

backups = sorted(path.parent.glob("order_intents.jsonl.bak.*"))
for old in backups[:-3]:
    old.unlink()
    print(f"Deleted old backup: {old.name}")
