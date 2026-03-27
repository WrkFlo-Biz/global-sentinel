#!/usr/bin/env python3
"""
GSS Disclosure Lag Backtest: AMD Congressional Trading Alpha Analysis
=====================================================================
Tests the hypothesis that AMD outperforms SPY in the 30-60-90 days
AFTER a politician disclosure that was itself 30-45 days delayed
from the actual trade date.

Uses monthly close data from Alpaca (Jan 2021 - Mar 2026).
"""

import json
import math
from datetime import datetime, timedelta
from typing import Optional

# ============================================================================
# AMD Monthly Close Data (from Alpaca, 2021-01 through 2026-03)
# ============================================================================
AMD_MONTHLY = {
    "2021-01": 85.64, "2021-02": 84.51, "2021-03": 78.50, "2021-04": 81.62,
    "2021-05": 80.08, "2021-06": 93.93, "2021-07": 106.19, "2021-08": 110.72,
    "2021-09": 102.90, "2021-10": 120.23, "2021-11": 158.37, "2021-12": 143.90,
    "2022-01": 114.25, "2022-02": 123.34, "2022-03": 109.34, "2022-04": 85.52,
    "2022-05": 101.86, "2022-06": 76.47, "2022-07": 94.47, "2022-08": 84.87,
    "2022-09": 63.36, "2022-10": 60.06, "2022-11": 77.63, "2022-12": 64.77,
    "2023-01": 75.15, "2023-02": 78.58, "2023-03": 98.01, "2023-04": 89.37,
    "2023-05": 118.21, "2023-06": 113.91, "2023-07": 114.40, "2023-08": 105.72,
    "2023-09": 102.82, "2023-10": 98.50, "2023-11": 121.16, "2023-12": 147.41,
    "2024-01": 167.69, "2024-02": 192.53, "2024-03": 180.49, "2024-04": 158.38,
    "2024-05": 166.90, "2024-06": 162.21, "2024-07": 144.48, "2024-08": 148.56,
    "2024-09": 164.08, "2024-10": 144.07, "2024-11": 137.18, "2024-12": 120.79,
    "2025-01": 115.95, "2025-02": 99.86, "2025-03": 102.74, "2025-04": 97.35,
    "2025-05": 110.73, "2025-06": 141.90, "2025-07": 176.31, "2025-08": 162.63,
    "2025-09": 161.79, "2025-10": 256.12, "2025-11": 217.53, "2025-12": 214.16,
    "2026-01": 236.73, "2026-02": 200.21, "2026-03": 199.45,
}

# SPY Monthly Close Data (from Alpaca, benchmark)
SPY_MONTHLY = {
    "2021-01": 370.14, "2021-02": 380.01, "2021-03": 396.33, "2021-04": 417.30,
    "2021-05": 420.19, "2021-06": 428.06, "2021-07": 438.51, "2021-08": 451.56,
    "2021-09": 429.14, "2021-10": 459.25, "2021-11": 455.56, "2021-12": 474.96,
    "2022-01": 449.91, "2022-02": 436.63, "2022-03": 451.64, "2022-04": 412.00,
    "2022-05": 412.93, "2022-06": 377.25, "2022-07": 411.99, "2022-08": 395.18,
    "2022-09": 357.18, "2022-10": 386.21, "2022-11": 407.68, "2022-12": 382.43,
    "2023-01": 406.48, "2023-02": 396.26, "2023-03": 409.39, "2023-04": 415.93,
    "2023-05": 417.85, "2023-06": 443.28, "2023-07": 457.79, "2023-08": 450.35,
    "2023-09": 427.48, "2023-10": 418.20, "2023-11": 456.40, "2023-12": 475.31,
    "2024-01": 482.88, "2024-02": 508.08, "2024-03": 523.07, "2024-04": 501.98,
    "2024-05": 527.37, "2024-06": 544.22, "2024-07": 550.81, "2024-08": 563.68,
    "2024-09": 573.76, "2024-10": 568.64, "2024-11": 602.55, "2024-12": 586.08,
    "2025-01": 601.82, "2025-02": 594.18, "2025-03": 559.39, "2025-04": 554.54,
    "2025-05": 589.39, "2025-06": 617.85, "2025-07": 632.08, "2025-08": 645.05,
    "2025-09": 666.18, "2025-10": 682.06, "2025-11": 683.39, "2025-12": 681.92,
    "2026-01": 691.97, "2026-02": 685.99, "2026-03": 681.31,
}

# ============================================================================
# Known Congressional AMD Trades (from public disclosure research)
# Format: (trade_date, disclosure_date, politician, party, chamber, action, amount_range, context)
# ============================================================================
CONGRESSIONAL_TRADES = [
    # CHIPS Act Era (2021-2022)
    ("2021-06-15", "2021-07-20", "Rep. Ro Khanna", "D", "House", "Purchase", "$15K-$50K",
     "Pre-CHIPS Act; sits on Armed Services Committee"),
    ("2021-09-20", "2021-10-25", "Rep. Michael McCaul", "R", "House", "Purchase", "$15K-$50K",
     "CHIPS Act advocacy; Chair of Foreign Affairs Committee"),
    ("2022-01-18", "2022-02-15", "Rep. Marjorie Taylor Greene", "R", "House", "Purchase", "$1K-$15K",
     "Early 2022 semiconductor investment"),
    ("2022-05-10", "2022-06-14", "Rep. Marjorie Taylor Greene", "R", "House", "Purchase", "$1K-$15K",
     "Pre-CHIPS Act passage (signed Aug 9, 2022)"),
    ("2022-07-15", "2022-08-16", "Rep. Michael McCaul", "R", "House", "Purchase", "$15K-$50K",
     "CHIPS Act passage period; co-sponsor"),
    ("2022-11-01", "2022-12-05", "Rep. Marjorie Taylor Greene", "R", "House", "Purchase", "$1K-$15K",
     "Post-CHIPS Act; AMD bottomed ~$60"),

    # AI Boom Era (2023-2024)
    ("2023-05-15", "2023-06-20", "Rep. Michael McCaul", "R", "House", "Purchase", "$15K-$50K",
     "Early AI semiconductor boom"),
    ("2023-10-10", "2023-11-10", "Rep. Marjorie Taylor Greene", "R", "House", "Purchase", "$1K-$15K",
     "Pre-AI earnings surge"),
    ("2024-02-15", "2024-03-18", "Rep. Michael McCaul", "R", "House", "Purchase", "$15K-$50K",
     "AI chip demand; AMD MI300X launch"),
    ("2024-04-10", "2024-05-12", "Rep. Marjorie Taylor Greene", "R", "House", "Purchase", "$1K-$15K",
     "Continued AI accumulation"),
    ("2024-06-21", "2024-07-22", "Rep. Michael McCaul", "R", "House", "Purchase", "$15K-$50K",
     "AI boom; McCaul spouse account"),
    ("2024-10-31", "2024-11-25", "Rep. Michael McCaul", "R", "House", "Purchase", "$15K-$50K",
     "Post-election; McCaul spouse account"),
    ("2024-11-04", "2024-12-06", "Rep. Marjorie Taylor Greene", "R", "House", "Purchase", "$1K-$15K",
     "Post-election semiconductor bet"),

    # Recent Period (2025-2026)
    ("2025-01-08", "2025-01-21", "Rep. Marjorie Taylor Greene", "R", "House", "Purchase", "$1K-$15K",
     "New Congress; AI policy agenda"),
    ("2025-02-13", "2025-02-27", "Sen. Markwayne Mullin", "R", "Senate", "Sale", "$50K-$100K",
     "Sold during AMD weakness"),
    ("2025-04-15", "2025-05-12", "Rep. Jefferson Shreve", "R", "House", "Sale", "$50K-$200K",
     "Sold AMD during tariff uncertainty"),
    ("2025-07-30", "2025-08-25", "Rep. Cleo Fields", "D", "House", "Purchase", "$50K-$100K",
     "Bought during AMD rebound"),
    ("2025-11-25", "2025-12-20", "Rep. Marjorie Taylor Greene", "R", "House", "Purchase", "$1K-$15K",
     "Post AMD earnings surge to $256"),
    ("2026-02-03", "2026-02-24", "Rep. Cleo Fields", "D", "House", "Purchase", "$50K-$100K",
     "Semiconductor investment; Morgan Stanley account"),
]


def get_price(ticker_data: dict, date_str: str) -> Optional[float]:
    """Get the closest monthly close price for a given date.
    Uses the month of the date as the key.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    key = dt.strftime("%Y-%m")
    if key in ticker_data:
        return ticker_data[key]
    return None


def get_price_interpolated(ticker_data: dict, date_str: str) -> Optional[float]:
    """Interpolate price within a month based on day of month.
    Uses linear interpolation between previous month's close and current month's close.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    curr_key = dt.strftime("%Y-%m")
    prev_dt = dt.replace(day=1) - timedelta(days=1)
    prev_key = prev_dt.strftime("%Y-%m")

    if curr_key not in ticker_data:
        return None

    curr_close = ticker_data[curr_key]

    if prev_key not in ticker_data:
        return curr_close

    prev_close = ticker_data[prev_key]

    # Interpolate: day 1 = prev_close, day 30 = curr_close
    import calendar
    days_in_month = calendar.monthrange(dt.year, dt.month)[1]
    frac = dt.day / days_in_month
    return prev_close + (curr_close - prev_close) * frac


def get_future_price(ticker_data: dict, date_str: str, days_forward: int) -> Optional[float]:
    """Get the interpolated price N days after a given date."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    future_dt = dt + timedelta(days=days_forward)
    return get_price_interpolated(ticker_data, future_dt.strftime("%Y-%m-%d"))


def compute_return(entry_price: float, exit_price: float) -> float:
    """Compute percentage return."""
    return ((exit_price - entry_price) / entry_price) * 100


def run_backtest():
    """Run the disclosure lag backtest."""
    results = []
    purchases_only = []

    print("=" * 100)
    print("GSS DISCLOSURE LAG BACKTEST: AMD Congressional Trading Alpha Analysis")
    print("=" * 100)
    print()

    for trade in CONGRESSIONAL_TRADES:
        trade_date, disc_date, politician, party, chamber, action, amount, context = trade

        # Get price at disclosure date
        disc_price = get_price_interpolated(AMD_MONTHLY, disc_date)
        spy_disc_price = get_price_interpolated(SPY_MONTHLY, disc_date)

        if disc_price is None or spy_disc_price is None:
            continue

        # Compute forward returns from disclosure date
        result = {
            "trade_date": trade_date,
            "disclosure_date": disc_date,
            "politician": politician,
            "party": party,
            "action": action,
            "amount": amount,
            "context": context,
            "disc_price_amd": disc_price,
            "disc_price_spy": spy_disc_price,
        }

        for horizon in [30, 60, 90]:
            amd_future = get_future_price(AMD_MONTHLY, disc_date, horizon)
            spy_future = get_future_price(SPY_MONTHLY, disc_date, horizon)

            if amd_future and spy_future:
                amd_ret = compute_return(disc_price, amd_future)
                spy_ret = compute_return(spy_disc_price, spy_future)
                alpha = amd_ret - spy_ret
                result[f"amd_{horizon}d"] = amd_ret
                result[f"spy_{horizon}d"] = spy_ret
                result[f"alpha_{horizon}d"] = alpha
            else:
                result[f"amd_{horizon}d"] = None
                result[f"spy_{horizon}d"] = None
                result[f"alpha_{horizon}d"] = None

        results.append(result)
        if action == "Purchase":
            purchases_only.append(result)

    # ========================================================================
    # Print Results Table
    # ========================================================================
    print("RESULTS TABLE (All Trades)")
    print("-" * 100)
    header = f"{'Disc Date':<12} {'Action':<8} {'Politician':<28} {'AMD@Disc':>10} {'30d Ret':>8} {'60d Ret':>8} {'90d Ret':>8} {'30d Alpha':>10}"
    print(header)
    print("-" * 100)

    for r in results:
        amd_30 = f"{r['amd_30d']:+.1f}%" if r['amd_30d'] is not None else "N/A"
        amd_60 = f"{r['amd_60d']:+.1f}%" if r['amd_60d'] is not None else "N/A"
        amd_90 = f"{r['amd_90d']:+.1f}%" if r['amd_90d'] is not None else "N/A"
        alpha_30 = f"{r['alpha_30d']:+.1f}%" if r['alpha_30d'] is not None else "N/A"

        print(f"{r['disclosure_date']:<12} {r['action']:<8} {r['politician']:<28} ${r['disc_price_amd']:>8.2f} {amd_30:>8} {amd_60:>8} {amd_90:>8} {alpha_30:>10}")

    # ========================================================================
    # Statistical Summary - Purchases Only
    # ========================================================================
    print()
    print("=" * 100)
    print("STATISTICAL SUMMARY (Purchases Only - Following the Politicians)")
    print("=" * 100)

    for horizon in [30, 60, 90]:
        key_amd = f"amd_{horizon}d"
        key_spy = f"spy_{horizon}d"
        key_alpha = f"alpha_{horizon}d"

        amd_rets = [r[key_amd] for r in purchases_only if r[key_amd] is not None]
        spy_rets = [r[key_spy] for r in purchases_only if r[key_spy] is not None]
        alphas = [r[key_alpha] for r in purchases_only if r[key_alpha] is not None]

        if not amd_rets:
            continue

        avg_amd = sum(amd_rets) / len(amd_rets)
        avg_spy = sum(spy_rets) / len(spy_rets)
        avg_alpha = sum(alphas) / len(alphas)
        win_rate = sum(1 for a in alphas if a > 0) / len(alphas) * 100

        # Sharpe-like ratio (annualized from the horizon period)
        if len(alphas) > 1:
            mean_alpha = avg_alpha
            std_alpha = (sum((a - mean_alpha) ** 2 for a in alphas) / (len(alphas) - 1)) ** 0.5
            periods_per_year = 365 / horizon
            sharpe = (mean_alpha * periods_per_year) / (std_alpha * math.sqrt(periods_per_year)) if std_alpha > 0 else float('inf')
        else:
            sharpe = 0

        print(f"\n  {horizon}-Day Horizon ({len(amd_rets)} trades):")
        print(f"    Avg AMD Return:    {avg_amd:+.2f}%")
        print(f"    Avg SPY Return:    {avg_spy:+.2f}%")
        print(f"    Avg Alpha:         {avg_alpha:+.2f}%")
        print(f"    Win Rate (vs SPY): {win_rate:.0f}%")
        print(f"    Sharpe Ratio:      {sharpe:.2f}")

    # Best and Worst Trades (by 60-day alpha)
    valid_purchases = [r for r in purchases_only if r['alpha_60d'] is not None]
    if valid_purchases:
        best = max(valid_purchases, key=lambda x: x['alpha_60d'])
        worst = min(valid_purchases, key=lambda x: x['alpha_60d'])

        print(f"\n  Best Trade (60d alpha):  {best['politician']} on {best['disclosure_date']} "
              f"({best['alpha_60d']:+.1f}% alpha, context: {best['context']})")
        print(f"  Worst Trade (60d alpha): {worst['politician']} on {worst['disclosure_date']} "
              f"({worst['alpha_60d']:+.1f}% alpha, context: {worst['context']})")

    # ========================================================================
    # CHIPS Act Correlation Analysis
    # ========================================================================
    print()
    print("=" * 100)
    print("CHIPS ACT CORRELATION ANALYSIS")
    print("=" * 100)

    chips_trades = [r for r in purchases_only if "2022" in r['trade_date'] or "2021" in r['trade_date']]
    ai_trades = [r for r in purchases_only if "2023" in r['trade_date'] or "2024" in r['trade_date']]
    recent_trades = [r for r in purchases_only if "2025" in r['trade_date'] or "2026" in r['trade_date']]

    for label, subset in [("CHIPS Act Era (2021-2022)", chips_trades),
                          ("AI Boom Era (2023-2024)", ai_trades),
                          ("Recent Era (2025-2026)", recent_trades)]:
        alphas_60 = [r['alpha_60d'] for r in subset if r['alpha_60d'] is not None]
        if alphas_60:
            avg = sum(alphas_60) / len(alphas_60)
            win = sum(1 for a in alphas_60 if a > 0) / len(alphas_60) * 100
            print(f"\n  {label}: {len(alphas_60)} trades")
            print(f"    Avg 60d Alpha: {avg:+.2f}%")
            print(f"    Win Rate:      {win:.0f}%")

    # ========================================================================
    # Sale Signal Analysis
    # ========================================================================
    print()
    print("=" * 100)
    print("SALE SIGNAL ANALYSIS (Do politician sells predict AMD weakness?)")
    print("=" * 100)

    sales = [r for r in results if r['action'] == 'Sale']
    for r in sales:
        amd_60 = f"{r['amd_60d']:+.1f}%" if r['amd_60d'] is not None else "N/A"
        spy_60 = f"{r['spy_60d']:+.1f}%" if r['spy_60d'] is not None else "N/A"
        alpha_60 = f"{r['alpha_60d']:+.1f}%" if r['alpha_60d'] is not None else "N/A"
        print(f"  {r['politician']} sold on {r['trade_date']} (disclosed {r['disclosure_date']})")
        print(f"    AMD 60d: {amd_60}, SPY 60d: {spy_60}, Alpha: {alpha_60}")
        print(f"    Context: {r['context']}")
        print()

    # ========================================================================
    # Disclosure Lag Analysis
    # ========================================================================
    print("=" * 100)
    print("DISCLOSURE LAG TIMING ANALYSIS")
    print("=" * 100)

    lags = []
    for trade in CONGRESSIONAL_TRADES:
        trade_dt = datetime.strptime(trade[0], "%Y-%m-%d")
        disc_dt = datetime.strptime(trade[1], "%Y-%m-%d")
        lag = (disc_dt - trade_dt).days
        lags.append(lag)

    avg_lag = sum(lags) / len(lags)
    min_lag = min(lags)
    max_lag = max(lags)
    print(f"\n  Average Disclosure Lag: {avg_lag:.0f} days")
    print(f"  Min Lag: {min_lag} days")
    print(f"  Max Lag: {max_lag} days")

    # Check if AMD moved significantly between trade and disclosure
    print("\n  Price Movement During Lag (Trade Date -> Disclosure Date):")
    for trade in CONGRESSIONAL_TRADES:
        trade_date, disc_date, politician, _, _, action, _, _ = trade
        trade_price = get_price_interpolated(AMD_MONTHLY, trade_date)
        disc_price = get_price_interpolated(AMD_MONTHLY, disc_date)
        if trade_price and disc_price:
            lag_return = compute_return(trade_price, disc_price)
            print(f"    {politician:<28} {trade_date} -> {disc_date}: {lag_return:+.1f}% ({action})")

    # ========================================================================
    # Summary JSON output
    # ========================================================================
    summary = {
        "backtest": "AMD Disclosure Lag",
        "period": "2021-01 to 2026-03",
        "total_trades": len(results),
        "purchases": len(purchases_only),
        "sales": len(sales),
    }

    for horizon in [30, 60, 90]:
        key = f"alpha_{horizon}d"
        alphas = [r[key] for r in purchases_only if r[key] is not None]
        if alphas:
            summary[f"avg_alpha_{horizon}d"] = round(sum(alphas) / len(alphas), 2)
            summary[f"win_rate_{horizon}d"] = round(sum(1 for a in alphas if a > 0) / len(alphas) * 100, 1)

    print()
    print("=" * 100)
    print("SUMMARY JSON")
    print("=" * 100)
    print(json.dumps(summary, indent=2))

    return results, summary


if __name__ == "__main__":
    results, summary = run_backtest()
