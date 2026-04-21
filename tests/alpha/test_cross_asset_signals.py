from src.alpha.cross_asset_signals import CrossAssetSignals


def _rich_market_data() -> dict:
    return {
        "TLT": {"price": 94.5, "change_pct": 0.8},
        "SPY": {"price": 501.0, "change_pct": 0.0},
        "HYG": {"price": 78.1, "change_pct": -0.35},
        "DXY": {"price": 107.2, "change_pct": 0.65},
        "USDJPY": {"price": 153.0, "change_pct": -0.55},
        "USDCAD": {"price": 1.33, "change_pct": -0.4},
        "CL": {"price": 83.0, "change_pct": 2.2},
        "UGA": {"price": 65.0, "change_pct": 0.2},
        "JETS": {"price": 20.0, "change_pct": -0.1},
        "VIX_F1": {"price": 24.0},
        "VIX_F2": {"price": 20.0},
        "ES": {"price": 5075.0, "change_pct": 0.9},
        "XLE": {"price": 90.5, "change_pct": 0.2},
        "GLD": {"price": 205.0, "change_pct": 1.0},
        "GDX": {"price": 31.0, "change_pct": 0.2},
        "SLV": {"price": 24.0, "change_pct": 0.1},
        "NG": {"price": 2.4, "change_pct": 2.4},
        "MOS": {"price": 45.0, "change_pct": 0.4},
        "DBA": {"price": 28.0, "change_pct": 0.1},
    }


def test_scan_keeps_legacy_api_and_adds_propagation_router_outputs():
    scanner = CrossAssetSignals()
    output = scanner.scan(market_data=_rich_market_data())

    # Legacy keys must remain available for existing callers.
    assert "bond_equity_signals" in output
    assert "currency_signals" in output
    assert "commodity_cascade_signals" in output
    assert "vix_term_structure" in output
    assert "futures_equity_basis" in output

    # New structured propagation router outputs.
    assert "cross_asset_propagation_map" in output
    assert "event_to_basket_routes" in output
    assert "ranked_baskets" in output
    assert "asset_class_links" in output
    assert "propagation_router_summary" in output

    propagation = output["cross_asset_propagation_map"]
    assert propagation
    assert any(route["signal"] == "bond_equity_divergence" for route in propagation)
    assert any(route["signal"] == "dxy_em_pressure" for route in propagation)


def test_propagation_router_ranks_baskets_and_tracks_cross_asset_links():
    scanner = CrossAssetSignals()
    output = scanner.scan(market_data=_rich_market_data())

    ranked = output["ranked_baskets"]
    assert ranked
    assert all(
        ranked[i]["route_score"] >= ranked[i + 1]["route_score"]
        for i in range(len(ranked) - 1)
    )

    # Ensure all major asset classes are represented in ranked baskets.
    asset_classes = {row["asset_class"] for row in ranked}
    assert {"equity", "bond", "yield", "fx", "future", "option"}.issubset(asset_classes)

    links = output["asset_class_links"]
    assert links
    assert any(
        link["from_asset_class"] == "bond" and link["to_asset_class"] == "yield"
        for link in links
    )


def test_commodity_signal_enrichment_routes_to_baskets():
    scanner = CrossAssetSignals()
    output = scanner.scan(
        market_data={
            "CL": {"price": 84.0, "change_pct": 2.3},
            "UGA": {"price": 66.0, "change_pct": 0.1},
            "JETS": {"price": 19.5, "change_pct": 0.0},
        }
    )

    commodity = output["commodity_cascade_signals"]
    assert commodity
    assert commodity[0]["signal"] == "oil_airline_fuel_cascade"
    assert commodity[0]["confidence"] >= 0.6

    routes = output["event_to_basket_routes"]
    assert any(row["signal"] == "oil_airline_fuel_cascade" for row in routes)
