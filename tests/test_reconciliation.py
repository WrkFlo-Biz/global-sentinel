from src.execution.order_book import OrderBook, OrderState
from src.execution.reconciliation import Reconciler


def test_reconciler_detects_mismatch(tmp_path):
    book = OrderBook(repo_root=tmp_path)
    order = book.create_order(
        symbol="XLE",
        direction="long",
        quantity=10,
        strategy="energy",
        account="day_trade",
        price_type="market",
    )
    for state in [OrderState.APPROVED, OrderState.VALIDATED, OrderState.SUBMITTED, OrderState.ACKNOWLEDGED]:
        book.transition(order.order_id, state, state.value)
    book.transition(order.order_id, OrderState.FILLED, "filled", {"filled_quantity": 10, "avg_fill_price": 100.0})
    rec = Reconciler().reconcile(book, {"positions": [{"symbol": "XLE", "qty": 8}], "cash": 1000.0, "expected_cash": 1000.0})
    assert rec["status"] == "discrepancies_found"
    assert rec["position_mismatches"][0]["symbol"] == "XLE"
