"""De-escalation Detector — flags ceasefire / peace signals for human review."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class DeescalationDetector:
    """Scan bridge results and scorecards for ceasefire / de-escalation language.

    This module **never** auto-closes positions.  It surfaces signals and
    flags positions that a human should review.
    """

    CEASEFIRE_KEYWORDS: list[str] = [
        "ceasefire",
        "negotiate",
        "diplomatic",
        "de-escalation",
        "talks",
        "truce",
        "peace",
        "surrender",
        "withdrawal",
        "agreement",
        "armistice",
        "treaty",
    ]

    def __init__(self) -> None:
        self._last_result: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def check(
        self,
        bridge_results: dict[str, Any] | None = None,
        scorecard: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Scan inputs for de-escalation signals.

        Parameters
        ----------
        bridge_results:
            Combined output from ingestion bridges (GDELT, Exa AI,
            sentiment, etc.).
        scorecard:
            Latest regime scorecard or scoring output.

        Returns
        -------
        dict with keys:
            detected, confidence, signals, portfolio_impact_estimate,
            flagged_positions, alert_message.
        """
        signals: list[dict[str, Any]] = []

        if bridge_results is not None:
            signals.extend(self._scan_bridge_results(bridge_results))

        if scorecard is not None:
            signals.extend(self._scan_scorecard(scorecard))

        confidence = self._compute_confidence(signals)
        detected = confidence > 0.0

        portfolio_impact = self._estimate_portfolio_impact()
        flagged = self._flag_positions(bridge_results, scorecard)

        alert_message: str | None = None
        if confidence > 0.7:
            alert_message = (
                f"CEASEFIRE SIGNAL ({confidence:.2f}) — "
                "review ALL short positions + UVXY + oil longs"
            )
            logger.warning("De-escalation alert: %s", alert_message)

        result: dict[str, Any] = {
            "detected": detected,
            "confidence": round(confidence, 4),
            "signals": signals,
            "portfolio_impact_estimate": portfolio_impact,
            "flagged_positions": flagged,
            "alert_message": alert_message,
        }

        self._last_result = result
        logger.info(
            "DeescalationDetector.check -> detected=%s  confidence=%.4f  signals=%d",
            detected,
            confidence,
            len(signals),
        )
        return result

    # ------------------------------------------------------------------
    # Scanning helpers
    # ------------------------------------------------------------------

    def _scan_bridge_results(
        self, bridge_results: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Walk bridge_results looking for keyword hits."""
        hits: list[dict[str, Any]] = []

        # --- GDELT events ---
        for event in bridge_results.get("gdelt_events", []):
            text = event.get("title", "") + " " + event.get("summary", "")
            matches = self._match_keywords(text)
            if matches:
                hits.append(
                    {
                        "source": "gdelt",
                        "text": text.strip()[:300],
                        "keyword_matches": matches,
                        "timestamp": event.get(
                            "timestamp",
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    }
                )

        # --- Exa AI articles ---
        for article in bridge_results.get("exa_articles", []):
            text = article.get("title", "") + " " + article.get("snippet", "")
            matches = self._match_keywords(text)
            if matches:
                hits.append(
                    {
                        "source": "exa_ai",
                        "text": text.strip()[:300],
                        "keyword_matches": matches,
                        "timestamp": article.get(
                            "published",
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    }
                )

        # --- Sentiment payload ---
        sentiment_text = json.dumps(
            bridge_results.get("sentiment", {}), default=str
        )
        matches = self._match_keywords(sentiment_text)
        if matches:
            hits.append(
                {
                    "source": "sentiment",
                    "text": sentiment_text[:300],
                    "keyword_matches": matches,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        # --- Generic text blobs (catch-all) ---
        for key in ("news_headlines", "raw_text", "briefing"):
            blob = bridge_results.get(key)
            if isinstance(blob, str):
                matches = self._match_keywords(blob)
                if matches:
                    hits.append(
                        {
                            "source": key,
                            "text": blob[:300],
                            "keyword_matches": matches,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )

        return hits

    def _scan_scorecard(
        self, scorecard: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Check scorecard narrative fields for ceasefire language."""
        hits: list[dict[str, Any]] = []
        for field in ("narrative", "summary", "regime_label", "notes"):
            text = scorecard.get(field, "")
            if not isinstance(text, str):
                continue
            matches = self._match_keywords(text)
            if matches:
                hits.append(
                    {
                        "source": f"scorecard.{field}",
                        "text": text[:300],
                        "keyword_matches": matches,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
        return hits

    def _match_keywords(self, text: str) -> list[str]:
        """Return list of ceasefire keywords found in *text* (case-insensitive)."""
        lower = text.lower()
        return [kw for kw in self.CEASEFIRE_KEYWORDS if kw in lower]

    # ------------------------------------------------------------------
    # Impact estimation
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_portfolio_impact() -> dict[str, Any]:
        """Rough estimate if oil drops 20% and shorts squeeze 10%.

        This is a static heuristic — real P&L requires live portfolio data.
        """
        return {
            "scenario": "oil -20%, short squeeze +10%",
            "oil_long_drawdown_pct": -20.0,
            "short_squeeze_loss_pct": -10.0,
            "vol_product_decay_pct": -15.0,
            "note": (
                "Estimates are directional only. "
                "Run full mark-to-market for actual exposure."
            ),
        }

    @staticmethod
    def _flag_positions(
        bridge_results: dict[str, Any] | None = None,
        scorecard: dict[str, Any] | None = None,
    ) -> list[str]:
        """Return position categories that should be reviewed.

        Always flags shorts, vol positions, and oil longs regardless of
        input data — those are the categories most exposed to a
        de-escalation event.
        """
        flagged = [
            "all_short_positions",
            "vol_long_positions (UVXY, VXX, VIXY)",
            "oil_long_positions (USO, UCO, XLE, OIH)",
            "defense_longs (LMT, RTX, NOC, GD, BA)",
        ]
        return flagged

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_confidence(signals: list[dict[str, Any]]) -> float:
        """Heuristic confidence based on number and diversity of signals.

        - 1 hit  -> 0.3
        - 2 hits -> 0.5
        - 3 hits -> 0.7
        - 4+ hits -> 0.85
        - 6+ unique sources -> 0.95
        """
        if not signals:
            return 0.0

        n = len(signals)
        unique_sources = len({s["source"] for s in signals})

        if unique_sources >= 6:
            return 0.95
        if n >= 4:
            return 0.85
        if n >= 3:
            return 0.70
        if n >= 2:
            return 0.50
        return 0.30

    # ------------------------------------------------------------------
    # Telegram formatting
    # ------------------------------------------------------------------

    def format_telegram(self) -> str:
        """Format the last check result for Telegram alerting."""
        if self._last_result is None:
            return "No de-escalation check has been run yet."

        conf = self._last_result["confidence"]
        n_signals = len(self._last_result["signals"])
        flagged = ", ".join(self._last_result["flagged_positions"])

        if conf > 0.7:
            header = f"\u26a0\ufe0f CEASEFIRE SIGNAL ({conf:.2f})"
        else:
            header = f"\U0001f7e2 De-escalation check ({conf:.2f})"

        lines = [
            header,
            f"Signals detected: {n_signals}",
            f"Flagged for review: {flagged}",
        ]

        if self._last_result.get("alert_message"):
            lines.append("")
            lines.append(self._last_result["alert_message"])

        return "\n".join(lines)
