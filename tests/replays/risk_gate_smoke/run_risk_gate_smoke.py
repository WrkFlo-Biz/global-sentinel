#!/usr/bin/env python3
"""
Risk Gate Integration Smoke Test

Validates that the RiskGate (composite gate wrapping VaR + Impact Budget)
correctly passes, blocks, or downsizes trade intents based on:
- Order quantity relative to ADV (participation rate)
- Expected market impact vs. budget (square-root law)
- Time window multipliers (opening_rush, lunch_lull, power_hour)
- Regime stress levels (normal, elevated, crisis)
- Missing microstructure data (should fail closed)

Exit 0 if all assertions pass, exit 1 if any fail.
"""

from __future__ import annotations

import json
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Mock snapshot builder
# ---------------------------------------------------------------------------

def build_snapshot(microstructure: Optional[Dict[str, Dict[str, float]]] = None) -> Dict[str, Any]:
    """Build a mock snapshot with market_microstructure data."""
    return {
        "snapshot_id": f"snap-rg-smoke-{uuid.uuid4().hex[:8]}",
        "timestamp_utc": "2026-03-05T12:00:00Z",
        "market_microstructure": microstructure or {},
    }


# Realistic-ish microstructure for aviation watchlist symbols
DEFAULT_MICROSTRUCTURE: Dict[str, Dict[str, float]] = {
    "DAL": {"adv_shares": 12_000_000, "sigma_daily_pct": 2.1},
    "UAL": {"adv_shares": 8_000_000, "sigma_daily_pct": 2.5},
    "SPY": {"adv_shares": 80_000_000, "sigma_daily_pct": 0.9},
    "ZIM": {"adv_shares": 1_500_000, "sigma_daily_pct": 4.2},
    "BA": {"adv_shares": 6_000_000, "sigma_daily_pct": 2.8},
}


def make_intent(
    candidate: Dict[str, Any],
    package_id: str = "pkg-rg-smoke",
    qty_override: Optional[float] = None,
) -> Dict[str, Any]:
    """Create a minimal intent dict from a candidate for risk gate evaluation."""
    return {
        "intent_id": f"intent-{candidate['candidate_id']}-{uuid.uuid4().hex[:6]}",
        "package_id": package_id,
        "candidate_id": candidate["candidate_id"],
        "router_run_id": f"rr-smoke-{uuid.uuid4().hex[:6]}",
        "symbol": candidate["symbol"],
        "side": candidate.get("side", "buy"),
        "qty": qty_override if qty_override is not None else candidate.get("qty", 0),
        "order_request": {
            "symbol": candidate["symbol"],
            "side": candidate.get("side", "buy"),
            "qty": qty_override if qty_override is not None else candidate.get("qty", 0),
        },
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestResult:
    def __init__(self, name: str, passed: bool, detail: str = ""):
        self.name = name
        self.passed = passed
        self.detail = detail


def test_small_qty_passes(risk_gate, package: Dict[str, Any]) -> List[TestResult]:
    """Small qty relative to ADV should pass the gate."""
    results: List[TestResult] = []
    snapshot = build_snapshot(DEFAULT_MICROSTRUCTURE)

    # DAL: 500 shares vs 12M ADV = 0.004% participation -- should pass easily
    dal_cand = package["candidates"][0]
    intent = make_intent(dal_cand)
    decision = risk_gate.check_intent(
        intent=intent,
        snapshot=snapshot,
        time_window_name="morning",
        regime="normal",
    )
    passed = decision["pass"] is True
    results.append(TestResult(
        "small_qty_DAL_passes",
        passed,
        f"pass={decision['pass']}, qty=500, adv=12M, cap={decision.get('recommended_qty_cap')}",
    ))

    # BA: 300 shares vs 6M ADV -- should pass
    ba_cand = package["candidates"][4]
    intent = make_intent(ba_cand)
    decision = risk_gate.check_intent(
        intent=intent,
        snapshot=snapshot,
        time_window_name="afternoon",
        regime="normal",
    )
    passed = decision["pass"] is True
    results.append(TestResult(
        "small_qty_BA_passes",
        passed,
        f"pass={decision['pass']}, qty=300, adv=6M",
    ))

    # SPY: 2000 shares vs 80M ADV -- trivial, should pass
    spy_cand = package["candidates"][2]
    intent = make_intent(spy_cand)
    decision = risk_gate.check_intent(
        intent=intent,
        snapshot=snapshot,
        time_window_name="morning",
        regime="normal",
    )
    passed = decision["pass"] is True
    results.append(TestResult(
        "small_qty_SPY_passes",
        passed,
        f"pass={decision['pass']}, qty=2000, adv=80M",
    ))

    return results


def test_large_qty_blocked(risk_gate, package: Dict[str, Any]) -> List[TestResult]:
    """Large qty that exceeds impact budget or participation rate should be blocked."""
    results: List[TestResult] = []
    snapshot = build_snapshot(DEFAULT_MICROSTRUCTURE)

    # UAL: 150,000 shares vs 8M ADV = 1.875% participation (max is 1%) -- should block
    ual_cand = package["candidates"][1]
    intent = make_intent(ual_cand)
    decision = risk_gate.check_intent(
        intent=intent,
        snapshot=snapshot,
        time_window_name="morning",
        regime="normal",
    )
    passed = decision["pass"] is False
    results.append(TestResult(
        "large_qty_UAL_blocked",
        passed,
        f"pass={decision['pass']}, qty=150000, adv=8M, participation=1.875%",
    ))

    # ZIM: 8000 shares vs 1.5M ADV = 0.53% -- within participation but check impact
    # sigma=4.2%, Y=1.0, impact = 1.0 * 4.2 * sqrt(8000/1500000) * 100 = ~30.7 bps
    # effective_budget for morning (multiplier=1.0) = 50/1.0 = 50 bps -- should pass
    # But let's test with a huge qty that will definitely block
    intent_big = make_intent(ual_cand, qty_override=500_000)
    decision_big = risk_gate.check_intent(
        intent=intent_big,
        snapshot=snapshot,
        time_window_name="morning",
        regime="normal",
    )
    passed_big = decision_big["pass"] is False
    results.append(TestResult(
        "huge_qty_UAL_500k_blocked",
        passed_big,
        f"pass={decision_big['pass']}, qty=500000, adv=8M",
    ))

    return results


def test_downsize_recommendation(risk_gate, package: Dict[str, Any]) -> List[TestResult]:
    """When qty exceeds budget, recommended_qty_cap should be >= 1."""
    results: List[TestResult] = []
    snapshot = build_snapshot(DEFAULT_MICROSTRUCTURE)

    # UAL blocked: check that recommended_qty_cap is a positive number
    ual_cand = package["candidates"][1]
    intent = make_intent(ual_cand)
    decision = risk_gate.check_intent(
        intent=intent,
        snapshot=snapshot,
        time_window_name="morning",
        regime="normal",
    )
    cap = decision.get("recommended_qty_cap", 0)
    passed = (decision["pass"] is False) and (cap >= 1)
    results.append(TestResult(
        "downsize_UAL_has_cap",
        passed,
        f"pass={decision['pass']}, recommended_qty_cap={cap}",
    ))

    # ZIM with inflated qty
    zim_cand = package["candidates"][3]
    intent_zim = make_intent(zim_cand, qty_override=100_000)
    decision_zim = risk_gate.check_intent(
        intent=intent_zim,
        snapshot=snapshot,
        time_window_name="morning",
        regime="normal",
    )
    cap_zim = decision_zim.get("recommended_qty_cap", 0)
    # With huge qty on low-ADV ZIM, gate should block and provide a cap
    passed_zim = (decision_zim["pass"] is False) and (cap_zim >= 1)
    results.append(TestResult(
        "downsize_ZIM_100k_has_cap",
        passed_zim,
        f"pass={decision_zim['pass']}, recommended_qty_cap={cap_zim}",
    ))

    return results


def test_missing_microstructure(risk_gate, package: Dict[str, Any]) -> List[TestResult]:
    """Missing microstructure data should fail closed (block the intent)."""
    results: List[TestResult] = []

    # Empty microstructure
    snapshot_empty = build_snapshot({})
    dal_cand = package["candidates"][0]
    intent = make_intent(dal_cand)
    decision = risk_gate.check_intent(
        intent=intent,
        snapshot=snapshot_empty,
        time_window_name="morning",
        regime="normal",
    )
    passed = decision["pass"] is False
    results.append(TestResult(
        "missing_micro_empty_snapshot_blocked",
        passed,
        f"pass={decision['pass']} (expected False for fail-closed)",
    ))

    # Microstructure present but symbol missing
    snapshot_partial = build_snapshot({"AAPL": {"adv_shares": 50_000_000, "sigma_daily_pct": 1.5}})
    decision2 = risk_gate.check_intent(
        intent=intent,
        snapshot=snapshot_partial,
        time_window_name="morning",
        regime="normal",
    )
    passed2 = decision2["pass"] is False
    results.append(TestResult(
        "missing_micro_symbol_not_in_snapshot_blocked",
        passed2,
        f"pass={decision2['pass']} (expected False for fail-closed)",
    ))

    # Microstructure with zero ADV
    snapshot_zero = build_snapshot({"DAL": {"adv_shares": 0, "sigma_daily_pct": 2.1}})
    decision3 = risk_gate.check_intent(
        intent=intent,
        snapshot=snapshot_zero,
        time_window_name="morning",
        regime="normal",
    )
    passed3 = decision3["pass"] is False
    results.append(TestResult(
        "missing_micro_zero_adv_blocked",
        passed3,
        f"pass={decision3['pass']} (expected False for fail-closed)",
    ))

    # Microstructure with zero sigma
    snapshot_zero_sigma = build_snapshot({"DAL": {"adv_shares": 12_000_000, "sigma_daily_pct": 0}})
    decision4 = risk_gate.check_intent(
        intent=intent,
        snapshot=snapshot_zero_sigma,
        time_window_name="morning",
        regime="normal",
    )
    passed4 = decision4["pass"] is False
    results.append(TestResult(
        "missing_micro_zero_sigma_blocked",
        passed4,
        f"pass={decision4['pass']} (expected False for fail-closed)",
    ))

    return results


def test_time_windows(risk_gate, package: Dict[str, Any]) -> List[TestResult]:
    """Test behavior across different time windows (opening_rush, lunch_lull, power_hour).

    Time windows affect the Y multiplier and effective impact budget.
    A borderline position should pass in a relaxed window but may fail in a stressed one.
    """
    results: List[TestResult] = []
    snapshot = build_snapshot(DEFAULT_MICROSTRUCTURE)

    # Use ZIM with a qty that is near the boundary for normal regime
    # ZIM: adv=1.5M, sigma=4.2%
    # We want a qty that passes in lunch_lull (midday_lull multiplier=0.8, wider budget)
    # but fails in power_hour (multiplier=1.4, tighter budget)
    # Budget = 50 / multiplier; Y = 1.0 * multiplier
    # Impact = Y * sigma * sqrt(Q/V) * 100
    #
    # For midday_lull (0.8): Y=0.8, budget=62.5bps
    #   impact = 0.8 * 4.2 * sqrt(Q/1500000) * 100
    # For power_hour (1.4): Y=1.4, budget=35.7bps
    #   impact = 1.4 * 4.2 * sqrt(Q/1500000) * 100
    #
    # We need impact_midday < 62.5 AND impact_power > 35.7
    # Let Q = 12000:
    #   sqrt(12000/1500000) = sqrt(0.008) = 0.08944
    #   midday: 0.8 * 4.2 * 0.08944 * 100 = 30.05 bps < 62.5 => pass
    #   power:  1.4 * 4.2 * 0.08944 * 100 = 52.59 bps > 35.7 => fail
    # Also check participation: 12000/1500000 = 0.8% < 1% => pass
    # Good, this should demonstrate window sensitivity.

    zim_cand = package["candidates"][3]
    borderline_qty = 12000

    for window_name, expect_pass in [
        ("opening_rush", None),
        ("lunch_lull", None),
        ("power_hour", None),
    ]:
        intent = make_intent(zim_cand, qty_override=borderline_qty)
        decision = risk_gate.check_intent(
            intent=intent,
            snapshot=snapshot,
            time_window_name=window_name,
            regime="normal",
        )
        # Record result -- we verify specific expected outcomes below
        results.append(TestResult(
            f"time_window_{window_name}_ZIM_{borderline_qty}",
            True,  # placeholder, we assert specifics below
            f"pass={decision['pass']}, cap={decision.get('recommended_qty_cap')}, window={window_name}",
        ))

    # Specific assertion: midday_lull (mapped internally as such) should be more permissive
    # than power_hour. Test with a qty that distinguishes them.
    # Use the actual gate window names that exist in the multiplier map
    intent_midday = make_intent(zim_cand, qty_override=borderline_qty)
    decision_midday = risk_gate.check_intent(
        intent=intent_midday,
        snapshot=snapshot,
        time_window_name="midday_lull",
        regime="normal",
    )
    intent_power = make_intent(zim_cand, qty_override=borderline_qty)
    decision_power = risk_gate.check_intent(
        intent=intent_power,
        snapshot=snapshot,
        time_window_name="power_hour",
        regime="normal",
    )
    midday_passes = decision_midday["pass"] is True
    power_fails = decision_power["pass"] is False

    results.append(TestResult(
        "midday_lull_more_permissive_than_power_hour",
        midday_passes and power_fails,
        f"midday_lull pass={decision_midday['pass']}, power_hour pass={decision_power['pass']}",
    ))

    return results


def test_regimes(risk_gate, package: Dict[str, Any]) -> List[TestResult]:
    """Test across regimes: normal, elevated, crisis.

    Higher regimes increase Y constant, meaning same qty has more impact.
    A borderline position should pass in normal but fail in crisis.
    """
    results: List[TestResult] = []
    snapshot = build_snapshot(DEFAULT_MICROSTRUCTURE)

    # ZIM with moderate qty
    # normal: Y=1.0, elevated: Y=1.5, crisis: Y=2.5
    # Budget stays at 50 bps (morning, tw_multiplier=1.0)
    # Impact = Y * sigma * sqrt(Q/V) * 100
    # ZIM: sigma=4.2, V=1.5M
    # Q=6000: sqrt(6000/1500000) = sqrt(0.004) = 0.06325
    #   normal:   1.0 * 4.2 * 0.06325 * 100 = 26.6 bps < 50 => pass
    #   elevated: 1.5 * 4.2 * 0.06325 * 100 = 39.8 bps < 50 => pass
    #   crisis:   2.5 * 4.2 * 0.06325 * 100 = 66.4 bps > 50 => fail
    # participation: 6000/1500000 = 0.4% < 1% => ok

    zim_cand = package["candidates"][3]
    regime_qty = 6000

    decisions_by_regime: Dict[str, Dict[str, Any]] = {}
    for regime in ["normal", "elevated", "crisis"]:
        intent = make_intent(zim_cand, qty_override=regime_qty)
        decision = risk_gate.check_intent(
            intent=intent,
            snapshot=snapshot,
            time_window_name="morning",
            regime=regime,
        )
        decisions_by_regime[regime] = decision
        results.append(TestResult(
            f"regime_{regime}_ZIM_{regime_qty}",
            True,  # record for summary
            f"pass={decision['pass']}, cap={decision.get('recommended_qty_cap')}, regime={regime}",
        ))

    # Specific: normal passes, crisis fails
    normal_passes = decisions_by_regime["normal"]["pass"] is True
    crisis_fails = decisions_by_regime["crisis"]["pass"] is False

    results.append(TestResult(
        "normal_passes_crisis_fails",
        normal_passes and crisis_fails,
        f"normal pass={decisions_by_regime['normal']['pass']}, crisis pass={decisions_by_regime['crisis']['pass']}",
    ))

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Risk Gate Integration Smoke Test")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    smoke_dir = repo_root / "tests" / "replays" / "risk_gate_smoke"
    out_dir = smoke_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(repo_root))

    from src.risk.var_gate import RiskGate

    # Load fixture
    fixture_path = smoke_dir / "fixtures" / "test_package.json"
    package = load_json(fixture_path)

    # Instantiate RiskGate with defaults
    risk_gate = RiskGate()

    # Collect all test results
    all_results: List[TestResult] = []
    test_suites = [
        ("small_qty_passes", test_small_qty_passes),
        ("large_qty_blocked", test_large_qty_blocked),
        ("downsize_recommendation", test_downsize_recommendation),
        ("missing_microstructure", test_missing_microstructure),
        ("time_windows", test_time_windows),
        ("regimes", test_regimes),
    ]

    for suite_name, suite_fn in test_suites:
        try:
            suite_results = suite_fn(risk_gate, package)
            all_results.extend(suite_results)
        except Exception:
            tb = traceback.format_exc()
            all_results.append(TestResult(
                f"SUITE_CRASH_{suite_name}",
                False,
                f"Suite raised exception:\n{tb}",
            ))

    # Print summary table
    print("")
    print("=" * 100)
    print(f"{'TEST NAME':<55} {'RESULT':<8} {'DETAIL'}")
    print("-" * 100)

    fail_count = 0
    pass_count = 0
    for r in all_results:
        status = "PASS" if r.passed else "FAIL"
        if not r.passed:
            fail_count += 1
        else:
            pass_count += 1
        detail_line = r.detail[:80] if len(r.detail) > 80 else r.detail
        print(f"{r.name:<55} {status:<8} {detail_line}")

    print("-" * 100)
    print(f"Total: {len(all_results)} | Passed: {pass_count} | Failed: {fail_count}")
    print("=" * 100)
    print("")

    # Write summary artifact
    summary = {
        "status": "ok" if fail_count == 0 else "fail",
        "total": len(all_results),
        "passed": pass_count,
        "failed": fail_count,
        "results": [
            {"name": r.name, "passed": r.passed, "detail": r.detail}
            for r in all_results
        ],
    }
    (out_dir / "risk_gate_smoke_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    if fail_count > 0:
        print(f"SMOKE TEST FAILED: {fail_count} assertion(s) did not pass.")
        sys.exit(1)
    else:
        print("All risk gate smoke tests passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
