from src.reports.tca_report import TCAReport


def test_tca_report_generates_metrics():
    orders = [
        {
            "order_id": "abc",
            "symbol": "XLE",
            "direction": "long",
            "filled_quantity": 10,
            "avg_fill_price": 101.0,
            "decision_price": 100.0,
            "arrival_price": 100.5,
            "commission": 1.0,
            "strategy": "energy",
            "filled_at": "2026-03-08T15:00:00+00:00",
        }
    ]
    md = {"XLE": {"vwap": 100.8, "twap": 100.7}}
    report = TCAReport().generate(orders, md)
    assert report["fills_analyzed"] == 1
    assert "energy" in report["by_strategy"]
