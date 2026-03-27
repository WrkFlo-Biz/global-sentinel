"""Join encoded candidate features with trade telemetry and research scores.

Produces a richer joined dataset for downstream training and analysis.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Union


def load_json(path: Path) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    return json.loads(path.read_text(encoding="utf-8"))


class TelemetryFeatureJoiner:
    def join(
        self,
        *,
        encoded_candidates: List[Dict[str, Any]],
        trade_outcomes: Dict[str, Any],
        research_score: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        trade_map = {
            str(r.get("symbol")): r
            for r in (trade_outcomes.get("trades") or [])
            if r.get("symbol")
        }

        joined = []
        for row in encoded_candidates:
            sym = str(row.get("symbol", ""))
            telemetry = trade_map.get(sym, {})
            out = dict(row)
            out["telemetry"] = telemetry
            if research_score:
                out["attached_research_score"] = research_score.get("research_score")
                out["recommended_influence"] = research_score.get("recommended_influence")
            joined.append(out)

        return {
            "schema_version": "telemetry_feature_join.v1",
            "row_count": len(joined),
            "rows": joined,
        }


def parse_args():
    p = argparse.ArgumentParser(description="Join features with telemetry")
    p.add_argument("--encoded-candidates-json", required=True)
    p.add_argument("--trade-outcomes-json", required=True)
    p.add_argument("--research-score-json", required=False)
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main():
    args = parse_args()

    encoded_candidates = load_json(Path(args.encoded_candidates_json))
    trade_outcomes = load_json(Path(args.trade_outcomes_json))
    research_score = None
    if args.research_score_json:
        research_score = load_json(Path(args.research_score_json))

    joined = TelemetryFeatureJoiner().join(
        encoded_candidates=encoded_candidates,
        trade_outcomes=trade_outcomes,
        research_score=research_score,
    )

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(joined, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
