"""Tests for new data bridges."""
import pytest
from src.bridges.maritime_bridge_v2 import MaritimeBridgeV2
from src.bridges.cds_sovereign_bridge import CDSSovereignBridge
from src.bridges.gpr_index_bridge import GPRIndexBridge
from src.bridges.semiconductor_supply_bridge import SemiconductorSupplyBridge


def test_maritime_baseline_poll():
    bridge = MaritimeBridgeV2()
    packets = bridge.poll()
    assert len(packets) == 5  # 5 chokepoints
    for p in packets:
        assert p["packet_type"] == "physical_flow_event"
        assert p["source"] == "maritime"
        assert 0 <= p["disruption_score"] <= 1
        assert "_lineage" in p
        assert p["packet_id"]


def test_maritime_disruption_scoring():
    bridge = MaritimeBridgeV2()
    # Low speed = high disruption
    score = bridge._compute_disruption_score(50, 3.0, "hormuz")
    assert score > 0.3
    # Normal conditions
    score_normal = bridge._compute_disruption_score(50, 12.0, "hormuz")
    assert score_normal < score


def test_cds_sovereign_poll():
    bridge = CDSSovereignBridge()
    packets = bridge.poll()
    assert len(packets) == 8  # 8 sovereigns in SOVEREIGN_SERIES
    for p in packets:
        assert p["packet_type"] == "macro_policy_event"
        assert p["source"] == "cds_sovereign"
        assert "spread_bps" in p
        assert "_lineage" in p


def test_cds_stress_computation():
    # 2x baseline = high stress
    stress = CDSSovereignBridge._compute_stress(700, 350)
    assert stress == 1.0
    # At baseline = no stress
    stress_normal = CDSSovereignBridge._compute_stress(350, 350)
    assert stress_normal == 0.0


def test_gpr_baseline():
    bridge = GPRIndexBridge()
    packets = bridge._baseline_packet()
    assert len(packets) == 1
    assert packets[0]["packet_type"] == "geopolitical_event"
    assert packets[0]["gpr_value"] == 100.0
    assert "_lineage" in packets[0]


def test_gpr_severity_mapping():
    assert GPRIndexBridge._map_severity(100) < 0.5
    assert GPRIndexBridge._map_severity(300) > 0.7
    assert GPRIndexBridge._map_severity(0) == 0.0


def test_semiconductor_poll():
    bridge = SemiconductorSupplyBridge()
    packets = bridge.poll()
    assert len(packets) > 0
    # Should have aggregate packet
    agg = [p for p in packets if p.get("event_type") == "supply_chain_aggregate"]
    assert len(agg) == 1
    assert "_lineage" in agg[0]


def test_semiconductor_stress():
    stress = SemiconductorSupplyBridge._compute_supply_stress(80, 100)
    assert stress == 0.2
    stress_zero = SemiconductorSupplyBridge._compute_supply_stress(100, 100)
    assert stress_zero == 0.0


def test_all_bridges_have_lineage():
    """Every new bridge must include _lineage in all packets."""
    bridges = [MaritimeBridgeV2(), CDSSovereignBridge(), SemiconductorSupplyBridge()]
    for bridge in bridges:
        packets = bridge.poll()
        for p in packets:
            assert "_lineage" in p, f"Missing _lineage in {p.get('source', 'unknown')}"
            assert "schema_version" in p["_lineage"]
