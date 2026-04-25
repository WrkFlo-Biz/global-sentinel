#!/usr/bin/env python3
from __future__ import annotations

"""
Interactive Brokers Bridge for Global Sentinel
Connects to IB Gateway via ib_async on the configured host/port.
Provides: get_account, get_positions, place_order, cancel_order, get_quote.
Similar interface to tastytrade_mcp_bridge.py.

Setup:
1. Install IB Gateway + IBC (already done at /opt/ibgateway, /opt/ibc)
2. pip install ib_async
3. Start gs-ibkr-gateway.service
"""
import asyncio
import json
import logging
import os
import sys
import datetime
from typing import Any, Optional

try:
    from ib_async import IB, Stock, Option, MarketOrder, LimitOrder, StopOrder, Contract
    HAS_IB = True
except ImportError:
    HAS_IB = False

logger = logging.getLogger("gs.ibkr_bridge")

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_ib: Optional[IB] = None


def get_connection_settings() -> dict[str, Any]:
    return {
        "host": os.getenv("IB_GATEWAY_HOST", "127.0.0.1"),
        "port": int(os.getenv("IB_GATEWAY_PORT", "4001")),
        "client_id": int(os.getenv("IB_CLIENT_ID", "1")),
    }


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


async def _get_ib() -> IB:
    """Return a connected IB instance, reconnecting if needed."""
    global _ib
    if _ib is not None and _ib.isConnected():
        return _ib
    if not HAS_IB:
        raise RuntimeError("ib_async not installed. Run: pip install ib_async")
    settings = get_connection_settings()
    _ib = IB()
    try:
        await _ib.connectAsync(
            settings["host"],
            settings["port"],
            clientId=settings["client_id"],
            timeout=15,
        )
        logger.info(
            "Connected to IB Gateway at %s:%s (clientId=%s)",
            settings["host"],
            settings["port"],
            settings["client_id"],
        )
    except Exception as e:
        _ib = None
        raise ConnectionError(
            f"Cannot connect to IB Gateway at {settings['host']}:{settings['port']}: {e}"
        )
    return _ib


async def disconnect():
    """Cleanly disconnect from IB Gateway."""
    global _ib
    if _ib is not None:
        _ib.disconnect()
        _ib = None


def _run(coro):
    """Run an async coroutine from sync context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # Already inside an event loop (e.g. Jupyter, ib_async nested)
        import nest_asyncio
        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

async def _get_account_info() -> dict:
    ib = await _get_ib()
    await ib.reqAccountSummaryAsync()
    summary = ib.accountSummary()
    result = {}
    for item in summary:
        result[item.tag] = item.value
    # Flatten to the fields our router expects
    return {
        "broker": "ibkr",
        "account": result.get("AccountCode", ""),
        "net_liquidation": result.get("NetLiquidation", "0"),
        "buying_power": result.get("BuyingPower", "0"),
        "cash_balance": result.get("TotalCashValue", "0"),
        "unrealized_pnl": result.get("UnrealizedPnL", "0"),
        "realized_pnl": result.get("RealizedPnL", "0"),
        "timestamp": iso_now(),
    }


def get_account_info() -> dict:
    try:
        return _run(_get_account_info())
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

async def _get_positions() -> list:
    ib = await _get_ib()
    positions = ib.positions()
    return [
        {
            "account": p.account,
            "symbol": p.contract.symbol,
            "sec_type": p.contract.secType,
            "exchange": p.contract.exchange,
            "currency": p.contract.currency,
            "quantity": float(p.position),
            "avg_cost": float(p.avgCost),
            "contract_id": p.contract.conId,
        }
        for p in positions
    ]


def get_positions() -> list:
    try:
        return _run(_get_positions())
    except Exception as e:
        return [{"error": str(e)}]


# ---------------------------------------------------------------------------
# Market data / quotes
# ---------------------------------------------------------------------------

async def _get_quote(symbol: str, sec_type: str = "STK", exchange: str = "SMART", currency: str = "USD") -> dict:
    ib = await _get_ib()
    if sec_type == "STK":
        contract = Stock(symbol, exchange, currency)
    elif sec_type == "OPT":
        contract = Contract(secType="OPT", symbol=symbol, exchange=exchange, currency=currency)
    else:
        contract = Contract(secType=sec_type, symbol=symbol, exchange=exchange, currency=currency)

    ib.qualifyContracts(contract)
    ticker = ib.reqMktData(contract, snapshot=True)
    # Wait for snapshot to fill (up to 5s)
    for _ in range(50):
        await asyncio.sleep(0.1)
        if ticker.last == ticker.last and ticker.last is not None:
            break

    return {
        "symbol": symbol,
        "bid": _safe_float(ticker.bid),
        "ask": _safe_float(ticker.ask),
        "last": _safe_float(ticker.last),
        "volume": _safe_float(ticker.volume),
        "high": _safe_float(ticker.high),
        "low": _safe_float(ticker.low),
        "close": _safe_float(ticker.close),
        "timestamp": iso_now(),
    }


def _safe_float(val):
    """Convert ib_async nan/None to None for JSON."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return f
    except (ValueError, TypeError):
        return None


def get_quote(symbol: str, sec_type: str = "STK", exchange: str = "SMART", currency: str = "USD") -> dict:
    try:
        return _run(_get_quote(symbol, sec_type, exchange, currency))
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

async def _place_order(
    symbol: str,
    qty: int,
    side: str,
    order_type: str = "market",
    limit_price: float = None,
    stop_price: float = None,
    sec_type: str = "STK",
    exchange: str = "SMART",
    currency: str = "USD",
    time_in_force: str = "DAY",
) -> dict:
    ib = await _get_ib()
    if sec_type == "STK":
        contract = Stock(symbol, exchange, currency)
    else:
        contract = Contract(secType=sec_type, symbol=symbol, exchange=exchange, currency=currency)

    ib.qualifyContracts(contract)
    action = "BUY" if side.lower() == "buy" else "SELL"
    qty = abs(int(qty))

    if order_type.lower() == "limit" and limit_price is not None:
        order = LimitOrder(action, qty, limit_price, tif=time_in_force)
    elif order_type.lower() == "stop" and stop_price is not None:
        order = StopOrder(action, qty, stop_price, tif=time_in_force)
    else:
        order = MarketOrder(action, qty, tif=time_in_force)

    trade = ib.placeOrder(contract, order)
    # Wait briefly for acknowledgement
    await asyncio.sleep(1)

    return {
        "status": "submitted",
        "order_id": trade.order.orderId,
        "perm_id": trade.order.permId,
        "symbol": symbol,
        "side": action,
        "qty": qty,
        "order_type": order_type,
        "limit_price": limit_price,
        "stop_price": stop_price,
        "order_status": trade.orderStatus.status,
        "timestamp": iso_now(),
    }


def place_order(
    symbol: str,
    qty: int,
    side: str,
    order_type: str = "market",
    limit_price: float = None,
    stop_price: float = None,
    sec_type: str = "STK",
    exchange: str = "SMART",
    currency: str = "USD",
    time_in_force: str = "DAY",
) -> dict:
    try:
        return _run(_place_order(symbol, qty, side, order_type, limit_price, stop_price, sec_type, exchange, currency, time_in_force))
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Cancel order
# ---------------------------------------------------------------------------

async def _cancel_order(order_id: int) -> dict:
    ib = await _get_ib()
    for trade in ib.openTrades():
        if trade.order.orderId == order_id:
            ib.cancelOrder(trade.order)
            await asyncio.sleep(0.5)
            return {
                "status": "cancel_requested",
                "order_id": order_id,
                "order_status": trade.orderStatus.status,
                "timestamp": iso_now(),
            }
    return {"error": f"Order {order_id} not found in open trades"}


def cancel_order(order_id: int) -> dict:
    try:
        return _run(_cancel_order(int(order_id)))
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Open orders
# ---------------------------------------------------------------------------

async def _get_orders() -> list:
    ib = await _get_ib()
    trades = ib.openTrades()
    return [
        {
            "order_id": t.order.orderId,
            "perm_id": t.order.permId,
            "symbol": t.contract.symbol,
            "action": t.order.action,
            "qty": float(t.order.totalQuantity),
            "order_type": t.order.orderType,
            "limit_price": _safe_float(t.order.lmtPrice),
            "status": t.orderStatus.status,
            "filled": float(t.orderStatus.filled),
            "remaining": float(t.orderStatus.remaining),
        }
        for t in trades
    ]


def get_orders() -> list:
    try:
        return _run(_get_orders())
    except Exception as e:
        return [{"error": str(e)}]


# ---------------------------------------------------------------------------
# Broker status (for multi-broker router)
# ---------------------------------------------------------------------------

async def _get_broker_status() -> dict:
    try:
        ib = await _get_ib()
        summary = await ib.reqAccountSummaryAsync()
        acct_data = {}
        for item in ib.accountSummary():
            acct_data[item.tag] = item.value
        return {
            "broker": "ibkr",
            "connected": True,
            "account": acct_data.get("AccountCode", ""),
            "net_liquidation": acct_data.get("NetLiquidation", "0"),
            "buying_power": acct_data.get("BuyingPower", "0"),
            "best_for": ["stocks", "options", "futures", "forex", "bonds"],
        }
    except Exception as e:
        return {"broker": "ibkr", "connected": False, "error": str(e)}


def get_broker_status() -> dict:
    return _run(_get_broker_status())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python ibkr_bridge.py <command> [args]")
        print("Commands: account, positions, quote <symbol>, buy <symbol> <qty>, sell <symbol> <qty>, orders, cancel <order_id>, status")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "account":
        print(json.dumps(get_account_info(), indent=2))
    elif cmd == "positions":
        print(json.dumps(get_positions(), indent=2))
    elif cmd == "quote" and len(sys.argv) > 2:
        print(json.dumps(get_quote(sys.argv[2]), indent=2))
    elif cmd == "buy" and len(sys.argv) > 3:
        otype = sys.argv[4] if len(sys.argv) > 4 else "market"
        lprice = float(sys.argv[5]) if len(sys.argv) > 5 else None
        print(json.dumps(place_order(sys.argv[2], int(sys.argv[3]), "buy", otype, lprice), indent=2))
    elif cmd == "sell" and len(sys.argv) > 3:
        otype = sys.argv[4] if len(sys.argv) > 4 else "market"
        lprice = float(sys.argv[5]) if len(sys.argv) > 5 else None
        print(json.dumps(place_order(sys.argv[2], int(sys.argv[3]), "sell", otype, lprice), indent=2))
    elif cmd == "orders":
        print(json.dumps(get_orders(), indent=2))
    elif cmd == "cancel" and len(sys.argv) > 2:
        print(json.dumps(cancel_order(int(sys.argv[2])), indent=2))
    elif cmd == "status":
        print(json.dumps(get_broker_status(), indent=2))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
