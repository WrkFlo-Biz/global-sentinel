from src.execution.order_book import OrderBook, OrderState


def test_order_book_lifecycle(tmp_path):
    book = OrderBook(repo_root=tmp_path)
    order = book.create_order(
        symbol="XLE",
        direction="long",
        quantity=10,
        strategy="energy",
        account="day_trade",
        price_type="limit",
        limit_price=90.0,
    )
    assert book.transition(order.order_id, OrderState.APPROVED, "approved")
    assert book.transition(order.order_id, OrderState.VALIDATED, "validated")
    assert book.transition(order.order_id, OrderState.SUBMITTED, "submitted")
    assert book.transition(order.order_id, OrderState.ACKNOWLEDGED, "acked", {"broker_order_id": "abc"})
    assert book.transition(order.order_id, OrderState.FILLED, "filled", {"filled_quantity": 10, "avg_fill_price": 90.5})
    assert book.orders[order.order_id].state == OrderState.FILLED
    assert book.orders[order.order_id].broker_order_id == "abc"


def test_order_book_invalid_transition(tmp_path):
    book = OrderBook(repo_root=tmp_path)
    order = book.create_order(
        symbol="GLD",
        direction="long",
        quantity=5,
        strategy="gold",
        account="medium_long",
        price_type="market",
    )
    assert not book.transition(order.order_id, OrderState.SUBMITTED, "skip ahead")
    assert book.orders[order.order_id].state == OrderState.IDEA


def test_order_book_daily_summary(tmp_path):
    book = OrderBook(repo_root=tmp_path)
    order = book.create_order(
        symbol="JETS",
        direction="short",
        quantity=4,
        strategy="airline_short",
        account="day_trade",
        price_type="market",
    )
    book.transition(order.order_id, OrderState.APPROVED, "approved")
    book.transition(order.order_id, OrderState.VALIDATED, "validated")
    book.transition(order.order_id, OrderState.SUBMITTED, "submitted")
    book.transition(order.order_id, OrderState.ACKNOWLEDGED, "acked")
    book.transition(order.order_id, OrderState.FILLED, "filled", {"filled_quantity": 4, "avg_fill_price": 12.0, "commission": 1.0, "slippage_bps": 4.0})
    summary = book.daily_summary(day_prefix=order.created_at[:10])
    assert summary["filled"] == 1
    assert summary["total_commission"] == 1.0
