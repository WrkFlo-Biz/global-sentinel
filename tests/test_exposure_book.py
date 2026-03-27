from src.risk.exposure_book import ExposureBook


class FakeAdapter:
    def __init__(self, equity, cash, buying_power, positions, open_orders=None):
        self._account = {
            "equity": equity,
            "cash": cash,
            "buying_power": buying_power,
        }
        self._positions = positions
        self._open_orders = open_orders or []

    def get_account_state(self):
        return dict(self._account)

    def list_positions(self):
        return list(self._positions)

    def list_open_orders(self):
        return list(self._open_orders)


def test_exposure_book_snapshot():
    book = ExposureBook(
        {
            "day_trade": FakeAdapter(100000, 80000, 120000, [{"symbol": "XLE", "market_value": 10000, "side": "long", "unrealized_pl": 100}]),
            "medium_long": FakeAdapter(500000, 450000, 550000, [{"symbol": "JETS", "market_value": 15000, "side": "short", "unrealized_pl": 50}]),
        }
    )
    snap = book.snapshot()
    assert snap["combined"]["total_equity"] == 600000
    assert "energy" in snap["by_sector"]
    assert "airlines" in snap["by_sector"]


def test_exposure_book_subtracts_pending_closes_from_effective_exposure():
    book = ExposureBook(
        {
            "day_trade": FakeAdapter(
                100000,
                80000,
                120000,
                [{"symbol": "XLE", "qty": 100, "market_value": 10000, "side": "long", "unrealized_pl": 100}],
                open_orders=[{"symbol": "XLE", "side": "sell", "qty": 100, "remaining_qty": 100}],
            ),
        }
    )
    snap = book.snapshot()
    assert snap["combined"]["raw_total_gross_exposure"] == 10000
    assert snap["combined"]["total_gross_exposure"] == 0
    assert snap["combined"]["pending_close_notional"] == 10000
    assert snap["accounts"]["day_trade"]["pending_close_orders"] == 1
