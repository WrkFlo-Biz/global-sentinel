"""Build normalized dataset snapshots for research / training / replay.

Captures regime state, packets, candidates, and microstructure
into a single versioned JSON document.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


class RegimeDatasetBuilder:

    def build(
        self,
        *,
        packets: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
        candidate_universe: List[Dict[str, Any]],
        market_microstructure: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "schema_version": "regime_dataset.v1",
            "regime_state": regime_state,
            "packet_count": len(packets),
            "packets": packets,
            "candidate_universe": candidate_universe,
            "market_microstructure": market_microstructure,
        }


def parse_args():
    p = argparse.ArgumentParser(description="Build regime dataset snapshot")
    p.add_argument("--packets-json", required=True)
    p.add_argument("--regime-state-json", required=True)
    p.add_argument("--candidate-json", required=True)
    p.add_argument("--market-micro-json", required=True)
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main():
    args = parse_args()

    packets = json.loads(Path(args.packets_json).read_text(encoding="utf-8"))
    regime_state = json.loads(Path(args.regime_state_json).read_text(encoding="utf-8"))
    candidate_universe = json.loads(Path(args.candidate_json).read_text(encoding="utf-8"))
    market_microstructure = json.loads(Path(args.market_micro_json).read_text(encoding="utf-8"))

    ds = RegimeDatasetBuilder().build(
        packets=packets,
        regime_state=regime_state,
        candidate_universe=candidate_universe,
        market_microstructure=market_microstructure,
    )

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ds, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
