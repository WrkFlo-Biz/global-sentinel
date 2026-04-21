#!/usr/bin/env python3
"""
Tastytrade MCP Bridge for Global Sentinel
Provides trading capabilities via Tastytrade's Python SDK.
Cash account = unlimited day trades on options.

Setup:
1. pip install tastytrade
2. Set env vars: TASTYTRADE_USERNAME, TASTYTRADE_PASSWORD
3. Add to Claude Code: claude mcp add tastytrade -s user -- python3 /path/to/tastytrade_mcp_bridge.py
"""
import json, os, sys, datetime
from pathlib import Path

try:
    from tastytrade import Session, Account
    from tastytrade.instruments import Equity, Option, get_option_chain
    from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType, PriceEffect
    from tastytrade.dxfeed import Quote
    HAS_TASTYTRADE = True
except ImportError:
    HAS_TASTYTRADE = False
    print("tastytrade not installed. Run: pip install tastytrade", file=sys.stderr)

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def get_session():
    username = os.getenv("TASTYTRADE_USERNAME", "")
    password = os.getenv("TASTYTRADE_PASSWORD", "")
    if not username or not password:
        return None, "TASTYTRADE_USERNAME and TASTYTRADE_PASSWORD env vars required"
    try:
        session = Session(username, password)
        return session, None
    except Exception as e:
        return None, str(e)

def get_account(session):
    accounts = Account.get_accounts(session)
    if not accounts:
        return None, "No accounts found"
    return accounts[0], None

# ============ ACCOUNT ============

def get_account_info():
    """Get account balance, buying power, positions."""
    session, err = get_session()
    if err:
        return {"error": err}
    account, err = get_account(session)
    if err:
        return {"error": err}
    balances = account.get_balances(session)
    return {
        "account_number": account.account_number,
        "cash_balance": str(balances.cash_balance),
        "buying_power": str(balances.derivative_buying_power),
        "net_liquidating_value": str(balances.net_liquidating_value),
        "day_trade_buying_power": str(getattr(balances, 'day_trade_buying_power', 'N/A')),
        "maintenance_excess": str(getattr(balances, 'maintenance_excess', 'N/A')),
    }

def get_positions():
    """Get all open positions."""
    session, err = get_session()
    if err:
        return {"error": err}
    account, err = get_account(session)
    if err:
        return {"error": err}
    positions = account.get_positions(session)
    return [{
        "symbol": p.symbol,
        "quantity": str(p.quantity),
        "quantity_direction": p.quantity_direction,
        "average_open_price": str(getattr(p, 'average_open_price', 'N/A')),
        "close_price": str(getattr(p, 'close_price', 'N/A')),
        "instrument_type": p.instrument_type,
    } for p in positions]

# ============ MARKET DATA ============

def get_option_chain_data(symbol, expiration_date=None):
    """Get option chain for a symbol."""
    session, err = get_session()
    if err:
        return {"error": err}
    try:
        chain = get_option_chain(session, symbol)
        results = []
        for exp_date, strikes in chain.items():
            if expiration_date and str(exp_date) != expiration_date:
                continue
            for strike, option in strikes.items():
                results.append({
                    "symbol": option.streamer_symbol if hasattr(option, 'streamer_symbol') else str(option),
                    "strike": str(strike),
                    "expiration": str(exp_date),
                    "option_type": option.option_type if hasattr(option, 'option_type') else 'unknown',
                })
            if len(results) > 50:
                break
        return {"chain": results[:50]}
    except Exception as e:
        return {"error": str(e)}

# ============ ORDERS ============

def place_equity_order(symbol, qty, side, order_type="market", limit_price=None, time_in_force="day"):
    """Place a stock order."""
    session, err = get_session()
    if err:
        return {"error": err}
    account, err = get_account(session)
    if err:
        return {"error": err}
    try:
        equity = Equity.get_equity(session, symbol)
        action = OrderAction.BUY_TO_OPEN if side == "buy" else OrderAction.SELL_TO_CLOSE
        tif = OrderTimeInForce.DAY if time_in_force == "day" else OrderTimeInForce.GTC

        if order_type == "limit" and limit_price:
            order = NewOrder(
                time_in_force=tif,
                order_type=OrderType.LIMIT,
                price=float(limit_price),
                price_effect=PriceEffect.DEBIT if side == "buy" else PriceEffect.CREDIT,
                legs=[equity.build_leg(int(qty), action)],
            )
        else:
            order = NewOrder(
                time_in_force=tif,
                order_type=OrderType.MARKET,
                legs=[equity.build_leg(int(qty), action)],
            )

        response = account.place_order(session, order)
        return {"status": "placed", "order": str(response)}
    except Exception as e:
        return {"error": str(e)}

def place_option_order(option_symbol, qty, side, order_type="limit", limit_price=None, time_in_force="day"):
    """Place an option order."""
    session, err = get_session()
    if err:
        return {"error": err}
    account, err = get_account(session)
    if err:
        return {"error": err}
    try:
        option = Option.get_option(session, option_symbol)
        action = OrderAction.BUY_TO_OPEN if side == "buy" else OrderAction.SELL_TO_CLOSE
        tif = OrderTimeInForce.DAY if time_in_force == "day" else OrderTimeInForce.GTC

        order = NewOrder(
            time_in_force=tif,
            order_type=OrderType.LIMIT if limit_price else OrderType.MARKET,
            price=float(limit_price) if limit_price else None,
            price_effect=PriceEffect.DEBIT if side == "buy" else PriceEffect.CREDIT,
            legs=[option.build_leg(int(qty), action)],
        )

        response = account.place_order(session, order)
        return {"status": "placed", "order": str(response)}
    except Exception as e:
        return {"error": str(e)}

def get_orders(status="open"):
    """Get orders by status."""
    session, err = get_session()
    if err:
        return {"error": err}
    account, err = get_account(session)
    if err:
        return {"error": err}
    try:
        if status == "open":
            orders = account.get_live_orders(session)
        else:
            orders = account.get_orders(session, per_page=20)
        return [{"id": str(o.id), "status": o.status, "legs": str(o.legs)} for o in orders[:20]]
    except Exception as e:
        return {"error": str(e)}

def cancel_order(order_id):
    """Cancel an order."""
    session, err = get_session()
    if err:
        return {"error": err}
    account, err = get_account(session)
    if err:
        return {"error": err}
    try:
        account.delete_order(session, order_id)
        return {"status": "cancelled", "order_id": order_id}
    except Exception as e:
        return {"error": str(e)}

# ============ MULTI-BROKER ROUTER ============

def get_broker_status():
    """Check Tastytrade broker status for the multi-broker router."""
    session, err = get_session()
    if err:
        return {"broker": "tastytrade", "connected": False, "error": err}
    account, err = get_account(session)
    if err:
        return {"broker": "tastytrade", "connected": False, "error": err}
    balances = account.get_balances(session)
    return {
        "broker": "tastytrade",
        "connected": True,
        "account_type": "cash",
        "unlimited_day_trades": True,
        "buying_power": str(balances.derivative_buying_power),
        "best_for": ["0DTE options", "day trades", "options spreads"],
    }

# ============ CLI ============

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python tastytrade_mcp_bridge.py <command> [args]")
        print("Commands: account, positions, chain <symbol>, buy <symbol> <qty>, sell <symbol> <qty>, orders, status")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "account":
        print(json.dumps(get_account_info(), indent=2))
    elif cmd == "positions":
        print(json.dumps(get_positions(), indent=2))
    elif cmd == "chain" and len(sys.argv) > 2:
        print(json.dumps(get_option_chain_data(sys.argv[2]), indent=2))
    elif cmd == "orders":
        print(json.dumps(get_orders(), indent=2))
    elif cmd == "status":
        print(json.dumps(get_broker_status(), indent=2))
    elif cmd == "buy" and len(sys.argv) > 3:
        print(json.dumps(place_equity_order(sys.argv[2], sys.argv[3], "buy"), indent=2))
    elif cmd == "sell" and len(sys.argv) > 3:
        print(json.dumps(place_equity_order(sys.argv[2], sys.argv[3], "sell"), indent=2))
    else:
        print(f"Unknown command: {cmd}")
