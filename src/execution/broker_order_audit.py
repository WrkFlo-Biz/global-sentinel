#!/usr/bin/env python3
"""
Broker Order Audit — classify, report, and optionally cancel orders.

Modes:
  --dry-run       Report only (default)
  --execute       Cancel stale/duplicate/crypto orders
  --verify-flat   Post-open check: verify both accounts have 0 positions
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.execution.alpaca_paper_adapter import AlpacaPaperAdapter

logger = logging.getLogger("global_sentinel.broker_order_audit")

# ── helpers ──────────────────────────────────────────────────────────

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        pass
    # Handle nanosecond-precision timestamps (Python < 3.11 only supports 6 digits)
    try:
        import re
        s = str(value).replace("Z", "+00:00")
        s = re.sub(r'(\.\d{6})\d+', r'\1', s)  # truncate to microseconds
        return datetime.fromisoformat(s)
    except Exception:
        return None


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


# ── classification buckets ───────────────────────────────────────────

BUCKET_PENDING_CLOSE = "pending_close"
BUCKET_STALE_OPEN = "stale_open"
BUCKET_DUPLICATE = "duplicate"
BUCKET_CRYPTO_ORPHAN = "crypto_orphan"
BUCKET_PARTIAL_FILL = "partial_fill"
BUCKET_REJECTED = "rejected"
BUCKET_POSITION_LEAK = "position_leak"
BUCKET_UNKNOWN = "unknown"

# Actions per bucket
ACTIONS = {
    BUCKET_PENDING_CLOSE: "wait",         # fills at 9:30
    BUCKET_STALE_OPEN: "cancel",          # auto-cancel
    BUCKET_DUPLICATE: "cancel_newer",     # cancel the newer duplicate
    BUCKET_CRYPTO_ORPHAN: "cancel",       # cancel
    BUCKET_PARTIAL_FILL: "review",        # manual review
    BUCKET_REJECTED: "log",              # log and clear
    BUCKET_POSITION_LEAK: "alert",        # position with no close order
    BUCKET_UNKNOWN: "review",
}

# Max order age before considered stale (hours)
STALE_THRESHOLD_HOURS = 24.0


class BrokerOrderAudit:
    """Classify broker orders and optionally execute cleanup actions."""

    ACCOUNT_LABELS = {
        "day_trade": "PA3F6696XKWK",
        "medium_long": "PA36T8OFBNXB",
    }

    def __init__(
        self,
        repo_root: str | Path | None = None,
        accounts: Optional[Dict[str, AlpacaPaperAdapter]] = None,
        dry_run: bool = True,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
        self.accounts = accounts or self._default_accounts_from_env()
        self.dry_run = dry_run

    # ── public API ───────────────────────────────────────────────────

    def run_audit(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Run full audit across all accounts. Returns the report dict."""
        observed_at = now or datetime.now(timezone.utc)
        report = {
            "schema_version": "broker_order_audit.v2",
            "generated_at_utc": observed_at.isoformat(),
            "mode": "dry_run" if self.dry_run else "execute",
            "accounts": {},
            "global_summary": {},
        }

        total_by_bucket: Dict[str, int] = defaultdict(int)
        total_actionable = 0
        total_cancelled = 0
        all_actionable_items: List[Dict[str, Any]] = []

        for acct_name, adapter in self.accounts.items():
            acct_label = self.ACCOUNT_LABELS.get(acct_name, acct_name)
            acct_report = self._audit_account(acct_name, acct_label, adapter, observed_at)
            report["accounts"][acct_name] = acct_report

            for bucket, count in acct_report["bucket_counts"].items():
                total_by_bucket[bucket] += count
            total_actionable += acct_report["actionable_count"]
            total_cancelled += acct_report["cancelled_count"]
            all_actionable_items.extend(acct_report["actionable_items"])

        critical = total_by_bucket.get(BUCKET_POSITION_LEAK, 0)
        report["global_summary"] = {
            "bucket_counts": dict(sorted(total_by_bucket.items())),
            "actionable_count": total_actionable,
            "cancelled_count": total_cancelled,
            "critical_count": critical,
            "flat_ready": total_actionable == 0 and critical == 0,
        }
        report["actionable_items"] = all_actionable_items

        # Render text report
        report["text_report"] = self._render_text_report(report, observed_at)
        return report

    def verify_flat(self) -> Dict[str, Any]:
        """Post-market-open check: verify all accounts have 0 positions."""
        results: Dict[str, Any] = {}
        all_flat = True
        for acct_name, adapter in self.accounts.items():
            positions = adapter.list_positions()
            open_orders = adapter.list_open_orders()
            is_flat = len(positions) == 0
            results[acct_name] = {
                "account_id": self.ACCOUNT_LABELS.get(acct_name, acct_name),
                "positions": len(positions),
                "open_orders": len(open_orders),
                "is_flat": is_flat,
                "position_symbols": [p.get("symbol") for p in positions],
            }
            if not is_flat:
                all_flat = False

        return {
            "verified_at_utc": iso_now(),
            "all_flat": all_flat,
            "accounts": results,
        }

    def save_report(
        self,
        report: Dict[str, Any],
        output_dir: str | Path = "reports/operational",
        stem: Optional[str] = None,
    ) -> Dict[str, str]:
        out_dir = (self.repo_root / output_dir) if not Path(output_dir).is_absolute() else Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = stem or datetime.now(timezone.utc).strftime("broker_order_audit_%Y%m%d_%H%M%S")
        json_path = out_dir / f"{suffix}.json"
        txt_path = out_dir / f"{suffix}.txt"

        # Save JSON (without the text_report to avoid duplication)
        json_report = {k: v for k, v in report.items() if k != "text_report"}
        json_path.write_text(json.dumps(json_report, indent=2, default=str), encoding="utf-8")

        # Save text report
        txt_path.write_text(report.get("text_report", ""), encoding="utf-8")

        return {"json_path": str(json_path), "txt_path": str(txt_path)}

    def send_telegram_alert(self, report: Dict[str, Any]) -> bool:
        """Send actionable items to Telegram if any exist."""
        actionable = report.get("actionable_items", [])
        summary = report.get("global_summary", {})
        if not actionable and summary.get("critical_count", 0) == 0:
            return False

        try:
            from src.monitoring.telegram_notifier import TelegramNotifier
            notifier = TelegramNotifier(repo_root=self.repo_root)
        except Exception as e:
            logger.warning("Cannot initialize TelegramNotifier: %s", e)
            return False

        lines = ["BROKER ORDER AUDIT ALERT"]
        lines.append(f"Mode: {'DRY RUN' if self.dry_run else 'EXECUTE'}")
        lines.append(f"Actionable: {summary.get('actionable_count', 0)}")
        lines.append(f"Critical: {summary.get('critical_count', 0)}")
        lines.append("")

        for item in actionable[:15]:  # Telegram message limit
            action = item.get("action", "review")
            symbol = item.get("symbol", "?")
            bucket = item.get("bucket", "?")
            detail = item.get("detail", "")
            lines.append(f"  {action.upper()} {symbol} [{bucket}] {detail}")

        if len(actionable) > 15:
            lines.append(f"  ... and {len(actionable) - 15} more")

        text = "\n".join(lines)
        try:
            notifier.send_message(text, bot_name="mo2darkbot")
            return True
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)
            return False

    # ── per-account audit ────────────────────────────────────────────

    def _audit_account(
        self,
        acct_name: str,
        acct_label: str,
        adapter: AlpacaPaperAdapter,
        observed_at: datetime,
    ) -> Dict[str, Any]:
        # Fetch state
        try:
            account_state = adapter.get_account_state()
        except Exception:
            account_state = {}
        positions = adapter.list_positions()
        open_orders = adapter.list_open_orders()

        positions_by_symbol = {
            str(p.get("symbol", "")).upper(): p for p in positions if p.get("symbol")
        }

        # Classify every order
        classified: List[Dict[str, Any]] = []
        for order in open_orders:
            row = self._classify_order(order, positions_by_symbol, observed_at)
            classified.append(row)

        # Detect duplicates (same symbol+side+type, keep oldest)
        self._mark_duplicates(classified)

        # Detect position leaks (positions with no close order)
        symbols_with_close = set()
        for row in classified:
            if row["bucket"] == BUCKET_PENDING_CLOSE:
                symbols_with_close.add(row["symbol"])

        leaks: List[Dict[str, Any]] = []
        for symbol, pos in positions_by_symbol.items():
            if symbol not in symbols_with_close:
                leaks.append({
                    "bucket": BUCKET_POSITION_LEAK,
                    "action": ACTIONS[BUCKET_POSITION_LEAK],
                    "symbol": symbol,
                    "side": pos.get("side", "?"),
                    "qty": safe_float(pos.get("qty")),
                    "market_value": safe_float(pos.get("market_value")),
                    "detail": f"position qty={safe_float(pos.get('qty')):.0f} with no close order",
                    "order_id": None,
                    "submitted_at": None,
                    "age_hours": None,
                })

        all_items = classified + leaks

        # Execute cancellations if not dry run
        cancelled_count = 0
        actionable_items: List[Dict[str, Any]] = []

        for item in all_items:
            action = item.get("action", "")
            if action in ("cancel", "cancel_newer"):
                actionable_items.append(item)
                if not self.dry_run and item.get("order_id"):
                    try:
                        adapter.cancel_order(item["order_id"])
                        item["cancel_result"] = "cancelled"
                        cancelled_count += 1
                        logger.info("Cancelled order %s (%s %s)", item["order_id"], item["symbol"], item["bucket"])
                    except Exception as e:
                        item["cancel_result"] = f"error: {e}"
                        logger.warning("Failed to cancel %s: %s", item["order_id"], e)
            elif action in ("review", "alert"):
                actionable_items.append(item)

        # Bucket counts
        bucket_counts: Dict[str, int] = defaultdict(int)
        for item in all_items:
            bucket_counts[item["bucket"]] += 1

        return {
            "account_id": acct_label,
            "equity": safe_float(account_state.get("equity")),
            "cash": safe_float(account_state.get("cash")),
            "buying_power": safe_float(account_state.get("buying_power")),
            "position_count": len(positions),
            "open_order_count": len(open_orders),
            "bucket_counts": dict(sorted(bucket_counts.items())),
            "actionable_count": len(actionable_items),
            "cancelled_count": cancelled_count,
            "classified_orders": classified,
            "position_leaks": leaks,
            "actionable_items": actionable_items,
        }

    # ── classification logic ─────────────────────────────────────────

    def _classify_order(
        self,
        order: Dict[str, Any],
        positions_by_symbol: Dict[str, Dict[str, Any]],
        observed_at: datetime,
    ) -> Dict[str, Any]:
        symbol = str(order.get("symbol", "")).upper()
        side = str(order.get("side", "")).lower()
        status = str(order.get("status") or order.get("broker_raw_status") or "").lower()
        order_type = str(order.get("type", "")).lower()
        qty = safe_float(order.get("qty"))
        filled_qty = safe_float(order.get("filled_qty"))
        remaining_qty = safe_float(order.get("remaining_qty"), max(qty - filled_qty, 0.0))
        submitted_at = parse_ts(order.get("submitted_at_utc"))
        age_hours = ((observed_at - submitted_at).total_seconds() / 3600.0) if submitted_at else None

        position = positions_by_symbol.get(symbol)
        position_qty = safe_float((position or {}).get("qty"))

        # Build base row
        row = {
            "order_id": order.get("order_id"),
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "status": status,
            "qty": qty,
            "filled_qty": filled_qty,
            "remaining_qty": remaining_qty,
            "limit_price": safe_float(order.get("limit_price")) or None,
            "submitted_at": str(submitted_at) if submitted_at else None,
            "age_hours": round(age_hours, 1) if age_hours is not None else None,
            "position_qty": position_qty,
            "bucket": BUCKET_UNKNOWN,
            "action": "review",
            "detail": "",
        }

        # ── Classification rules (priority order) ──

        # 1. Rejected
        if status in ("rejected",):
            row["bucket"] = BUCKET_REJECTED
            row["action"] = ACTIONS[BUCKET_REJECTED]
            row["detail"] = "rejected by broker"
            return row

        # 2. Crypto orphan (slash in symbol = crypto pair)
        if "/" in symbol:
            row["bucket"] = BUCKET_CRYPTO_ORPHAN
            row["action"] = ACTIONS[BUCKET_CRYPTO_ORPHAN]
            row["detail"] = f"crypto order on equity account"
            return row

        # 3. Partial fill
        if filled_qty > 0 and remaining_qty > 0:
            row["bucket"] = BUCKET_PARTIAL_FILL
            row["action"] = ACTIONS[BUCKET_PARTIAL_FILL]
            row["detail"] = f"filled {filled_qty:.0f}/{qty:.0f}, {remaining_qty:.0f} remaining"
            return row

        # 4. Pending close — sell order with matching long position, or buy order with matching short
        is_close = False
        if position:
            if position_qty > 0 and side == "sell":
                is_close = True
            elif position_qty < 0 and side == "buy":
                is_close = True

        if is_close:
            row["bucket"] = BUCKET_PENDING_CLOSE
            row["action"] = ACTIONS[BUCKET_PENDING_CLOSE]
            row["detail"] = f"close {abs(position_qty):.0f} {symbol} at market open"
            return row

        # 5. Stale open — order older than threshold with no matching position
        if age_hours is not None and age_hours > STALE_THRESHOLD_HOURS:
            row["bucket"] = BUCKET_STALE_OPEN
            row["action"] = ACTIONS[BUCKET_STALE_OPEN]
            row["detail"] = f"{side} {qty:.0f} @ {order_type} (submitted {age_hours:.0f}h ago)"
            return row

        # 6. Open order with no position match but < stale threshold
        if not position or position_qty == 0:
            # New entry order or orphaned — flag for review if it's a buy with no position
            if side == "buy":
                row["bucket"] = BUCKET_STALE_OPEN
                row["action"] = ACTIONS[BUCKET_STALE_OPEN]
                age_str = f"{age_hours:.0f}h ago" if age_hours else "unknown age"
                row["detail"] = f"buy {qty:.0f} @ {order_type} ({age_str}), no position"
                return row

        # Default
        row["detail"] = f"{side} {qty:.0f} {symbol} — no classification rule matched"
        return row

    def _mark_duplicates(self, classified: List[Dict[str, Any]]) -> None:
        """Find duplicate orders (same symbol+side) and mark newer ones."""
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in classified:
            if row["bucket"] == BUCKET_PENDING_CLOSE:
                key = f"{row['symbol']}:{row['side']}"
                groups[key].append(row)

        for key, items in groups.items():
            if len(items) <= 1:
                continue
            # Sort by submitted_at (oldest first), mark all but oldest as duplicate
            items.sort(key=lambda r: r.get("submitted_at") or "9999")
            for dup in items[1:]:
                dup["bucket"] = BUCKET_DUPLICATE
                dup["action"] = ACTIONS[BUCKET_DUPLICATE]
                dup["detail"] = f"duplicate close order ({len(items)} copies for {key})"

    # ── text report rendering ────────────────────────────────────────

    def _render_text_report(self, report: Dict[str, Any], observed_at: datetime) -> str:
        lines: List[str] = []
        lines.append("BROKER ORDER AUDIT REPORT")
        lines.append("=" * 50)
        lines.append(f"Timestamp: {observed_at.isoformat()}")
        lines.append(f"Mode: {'DRY RUN' if self.dry_run else 'EXECUTE'}")
        lines.append("")

        for acct_name, acct in report.get("accounts", {}).items():
            acct_id = acct.get("account_id", acct_name)
            lines.append(f"Account: {acct_name} ({acct_id})")
            lines.append(f"  Equity: ${acct.get('equity', 0):,.2f}  |  Cash: ${acct.get('cash', 0):,.2f}")
            lines.append(f"  Positions: {acct.get('position_count', 0)}  |  Open Orders: {acct.get('open_order_count', 0)}")
            lines.append("")

            buckets = acct.get("bucket_counts", {})

            # Pending closes
            pc = buckets.get(BUCKET_PENDING_CLOSE, 0)
            lines.append(f"  PENDING CLOSES: {pc}")
            if pc > 0:
                lines.append(f"    Ready to fill at market open")
            lines.append("")

            # Stale orders
            stale_items = [o for o in acct.get("classified_orders", []) if o["bucket"] == BUCKET_STALE_OPEN]
            lines.append(f"  STALE ORDERS: {len(stale_items)}")
            for item in stale_items[:10]:
                age = f"{item.get('age_hours', '?')}h old" if item.get('age_hours') else ""
                price = f"@ ${item.get('limit_price'):.2f}" if item.get("limit_price") else "@ market"
                cancelled = " -> CANCELLED" if item.get("cancel_result") == "cancelled" else ""
                lines.append(f"    ! {item['symbol']} {item['side']} {item['qty']:.0f} {price} ({age}) -> CANCEL{cancelled}")
            if len(stale_items) > 10:
                lines.append(f"    ... and {len(stale_items) - 10} more")
            lines.append("")

            # Duplicates
            dup_items = [o for o in acct.get("classified_orders", []) if o["bucket"] == BUCKET_DUPLICATE]
            lines.append(f"  DUPLICATES: {len(dup_items)}")
            for item in dup_items[:10]:
                cancelled = " -> CANCELLED" if item.get("cancel_result") == "cancelled" else ""
                lines.append(f"    ! {item['symbol']} {item['side']} {item['qty']:.0f} ({item['detail']}){cancelled}")
            lines.append("")

            # Crypto orphans
            crypto_items = [o for o in acct.get("classified_orders", []) if o["bucket"] == BUCKET_CRYPTO_ORPHAN]
            lines.append(f"  CRYPTO ORPHANS: {len(crypto_items)}")
            for item in crypto_items[:10]:
                cancelled = " -> CANCELLED" if item.get("cancel_result") == "cancelled" else ""
                lines.append(f"    ! {item['symbol']} {item['side']} ({item['detail']}){cancelled}")
            lines.append("")

            # Partial fills
            partial_items = [o for o in acct.get("classified_orders", []) if o["bucket"] == BUCKET_PARTIAL_FILL]
            lines.append(f"  PARTIAL FILLS: {len(partial_items)}")
            for item in partial_items[:10]:
                lines.append(f"    ? {item['symbol']} {item['side']} — {item['detail']}")
            lines.append("")

            # Rejected
            rejected_items = [o for o in acct.get("classified_orders", []) if o["bucket"] == BUCKET_REJECTED]
            lines.append(f"  REJECTED: {len(rejected_items)}")
            for item in rejected_items[:10]:
                lines.append(f"    x {item['symbol']} {item['side']} — {item['detail']}")
            lines.append("")

            # Position leaks
            leak_items = acct.get("position_leaks", [])
            lines.append(f"  POSITION LEAKS: {len(leak_items)}")
            for item in leak_items[:10]:
                lines.append(f"    !! {item['symbol']} {item['side']} qty={item['qty']:.0f} mkt_val=${item.get('market_value', 0):,.2f} — NO CLOSE ORDER")
            if not leak_items:
                lines.append(f"    None detected")
            lines.append("")

            # Account summary line
            ok_count = pc
            act_count = acct.get("actionable_count", 0)
            crit_count = len(leak_items)
            canc = acct.get("cancelled_count", 0)
            lines.append(f"  SUMMARY: {ok_count} pending (OK) | {act_count} actionable | {crit_count} critical | {canc} cancelled")
            lines.append("-" * 50)
            lines.append("")

        # Global summary
        gs = report.get("global_summary", {})
        lines.append("GLOBAL SUMMARY")
        lines.append(f"  Flat ready: {'YES' if gs.get('flat_ready') else 'NO'}")
        lines.append(f"  Actionable: {gs.get('actionable_count', 0)}")
        lines.append(f"  Critical: {gs.get('critical_count', 0)}")
        lines.append(f"  Cancelled: {gs.get('cancelled_count', 0)}")

        return "\n".join(lines)

    # ── account setup ────────────────────────────────────────────────

    def _default_accounts_from_env(self) -> Dict[str, AlpacaPaperAdapter]:
        accounts: Dict[str, AlpacaPaperAdapter] = {}
        seen: set[tuple[str, str]] = set()
        base_url = os.getenv("ALPACA_PAPER_BASE_URL") or os.getenv("ALPACA_BASE_URL")
        if base_url:
            base_url = base_url.rstrip("/")
            if base_url.endswith("/v2"):
                base_url = base_url[:-3]

        specs = {
            "day_trade": (
                os.getenv("ALPACA_API_KEY_DAYTRADE") or os.getenv("ALPACA_DAY_TRADE_KEY") or os.getenv("APCA_API_KEY_ID"),
                os.getenv("ALPACA_SECRET_KEY_DAYTRADE") or os.getenv("ALPACA_DAY_TRADE_SECRET") or os.getenv("APCA_API_SECRET_KEY"),
            ),
            "medium_long": (
                os.getenv("ALPACA_API_KEY_MEDIUM_LONG") or os.getenv("ALPACA_API_KEY_MEDIUMLONG") or os.getenv("ALPACA_API_KEY_MEDLONG") or os.getenv("ALPACA_MEDIUM_LONG_KEY"),
                os.getenv("ALPACA_SECRET_KEY_MEDIUM_LONG") or os.getenv("ALPACA_SECRET_KEY_MEDIUMLONG") or os.getenv("ALPACA_SECRET_KEY_MEDLONG") or os.getenv("ALPACA_MEDIUM_LONG_SECRET"),
            ),
        }
        for name, (key, secret) in specs.items():
            if not key or not secret:
                continue
            dedupe = (key, secret)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            accounts[name] = AlpacaPaperAdapter(api_key=key, api_secret=secret, base_url=base_url)
        return accounts
