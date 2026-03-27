#!/usr/bin/env python3
"""FinBERT News Sentiment Bridge.

Loads ProsusAI/finbert model to score recent headlines from Finnhub/GDELT/Google News
as positive/negative/neutral with confidence scores. Returns per-sector sentiment.

P1-3 enhancement — deployed 2026-03-25.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.bridges.base_bridge import BaseBridge, utc_now_iso

logger = logging.getLogger(__name__)

# Sector keyword mapping for classification
SECTOR_KEYWORDS = {
    "energy": ["oil", "crude", "brent", "wti", "opec", "energy", "gas", "lng", "petroleum", "refin", "pipeline"],
    "defense": ["defense", "military", "weapon", "missile", "lockheed", "raytheon", "northrop", "pentagon", "army", "navy", "drone"],
    "airlines": ["airline", "aviation", "flight", "airport", "delta air", "united air", "american air", "jet", "boeing"],
    "shipping": ["shipping", "tanker", "freight", "maritime", "container", "port", "vessel", "suez", "hormuz"],
    "technology": ["tech", "ai", "semiconductor", "chip", "nvidia", "apple", "microsoft", "google", "cloud"],
    "cybersecurity": ["cyber", "hack", "ransomware", "security breach", "crowdstrike", "palo alto", "zscaler"],
    "gold_metals": ["gold", "silver", "precious metal", "mining", "bullion"],
    "agriculture": ["fertilizer", "agriculture", "wheat", "corn", "crop", "food", "farming", "mosaic", "nutrien"],
    "nuclear": ["nuclear", "uranium", "reactor", "smr", "cameco", "enrichment"],
    "financials": ["bank", "fed", "interest rate", "treasury", "yield", "credit", "loan", "financial"],
    "geopolitical": ["iran", "israel", "russia", "ukraine", "china", "taiwan", "war", "peace", "ceasefire", "sanction", "tariff"],
}


class FinBERTSentimentBridge(BaseBridge):
    """Bridge that scores news headlines using FinBERT."""

    source = "finbert_sentiment_bridge"
    source_tier = "tier_3_research"
    trust_weight = 0.5
    freshness_ttl_minutes = 30

    def __init__(self, repo_root: Optional[Path] = None, config: Optional[dict] = None):
        super().__init__(repo_root=repo_root, config=config)
        self._model = None
        self._tokenizer = None
        self._model_loaded = False
        self._output_path = self.repo_root / "data" / "quantum_feed" / "finbert_sentiment.json"

    def _load_model(self):
        """Lazy-load FinBERT model."""
        if self._model_loaded:
            return True
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch

            logger.info("Loading FinBERT model (ProsusAI/finbert)...")
            self._tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
            self._model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert")
            self._model.eval()
            self._model_loaded = True
            logger.info("FinBERT model loaded successfully")
            return True
        except ImportError as exc:
            logger.error("transformers/torch not installed: %s", exc)
            return False
        except Exception as exc:
            logger.error("Failed to load FinBERT: %s", exc)
            return False

    def _score_headlines(self, headlines: List[str]) -> List[Dict]:
        """Score a batch of headlines with FinBERT."""
        import torch
        from torch.nn.functional import softmax

        if not headlines or not self._model:
            return []

        results = []
        # Process in batches of 16
        batch_size = 16
        labels = ["positive", "negative", "neutral"]

        for i in range(0, len(headlines), batch_size):
            batch = headlines[i:i + batch_size]
            try:
                inputs = self._tokenizer(
                    batch, padding=True, truncation=True,
                    max_length=128, return_tensors="pt"
                )
                with torch.no_grad():
                    outputs = self._model(**inputs)
                    probs = softmax(outputs.logits, dim=1)

                for j, headline in enumerate(batch):
                    prob_list = probs[j].tolist()
                    sentiment_idx = prob_list.index(max(prob_list))
                    results.append({
                        "headline": headline[:200],  # Truncate for storage
                        "sentiment": labels[sentiment_idx],
                        "confidence": round(max(prob_list), 4),
                        "positive_score": round(prob_list[0], 4),
                        "negative_score": round(prob_list[1], 4),
                        "neutral_score": round(prob_list[2], 4),
                    })
            except Exception as exc:
                logger.warning("FinBERT batch scoring failed: %s", exc)
                continue

        return results

    def _classify_sector(self, headline: str) -> List[str]:
        """Classify a headline into sectors based on keywords."""
        hl = headline.lower()
        sectors = []
        for sector, keywords in SECTOR_KEYWORDS.items():
            if any(kw in hl for kw in keywords):
                sectors.append(sector)
        return sectors if sectors else ["general"]

    def _gather_headlines(self) -> List[str]:
        """Gather recent headlines from available data sources."""
        headlines = []
        data_dir = self.repo_root / "data" / "quantum_feed"

        # Source 1: Latest signal
        try:
            signal_path = data_dir / "latest_signal.json"
            if signal_path.exists():
                data = json.loads(signal_path.read_text(encoding="utf-8"))
                for key in ("headlines", "breaking_news", "top_stories", "articles"):
                    items = data.get(key) or data.get("data", {}).get(key, [])
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, str):
                                headlines.append(item)
                            elif isinstance(item, dict):
                                title = item.get("title") or item.get("headline") or ""
                                if title:
                                    headlines.append(title)
                # Also check top_signals (dict of category -> list of headline strings)
                top_signals = data.get("top_signals", {})
                if isinstance(top_signals, dict):
                    for category, items in top_signals.items():
                        if isinstance(items, list):
                            for item in items:
                                if isinstance(item, str):
                                    # Strip source prefix like "[SERP] "
                                    clean = item.split("] ", 1)[-1] if item.startswith("[") else item
                                    headlines.append(clean)
        except Exception as exc:
            logger.debug("Failed to read latest_signal.json: %s", exc)

        # Source 1b: News impact bridge output
        try:
            news_path = data_dir / "news_impact.json"
            if news_path.exists():
                data = json.loads(news_path.read_text(encoding="utf-8"))
                for article in data.get("data", {}).get("articles", data.get("articles", [])):
                    if isinstance(article, dict):
                        title = article.get("title") or article.get("headline") or ""
                        if title:
                            headlines.append(title)
        except Exception as exc:
            logger.debug("Failed to read news_impact.json: %s", exc)

        # Source 1c: Exa search results
        try:
            for exa_file in data_dir.glob("exa_*.json"):
                data = json.loads(exa_file.read_text(encoding="utf-8"))
                results = data.get("data", {}).get("results", data.get("results", []))
                if isinstance(results, list):
                    for r in results[:30]:
                        title = r.get("title") or ""
                        if title:
                            headlines.append(title)
        except Exception as exc:
            logger.debug("Failed to read exa data: %s", exc)

        # Source 2: GDELT data
        try:
            for gdelt_file in data_dir.glob("gdelt*.json"):
                data = json.loads(gdelt_file.read_text(encoding="utf-8"))
                articles = data.get("articles") or data.get("data", {}).get("articles", [])
                if isinstance(articles, list):
                    for a in articles[:50]:
                        title = a.get("title") or a.get("headline") or ""
                        if title:
                            headlines.append(title)
        except Exception as exc:
            logger.debug("Failed to read GDELT data: %s", exc)

        # Source 3: Finnhub news if cached
        try:
            for finnhub_file in data_dir.glob("finnhub*.json"):
                data = json.loads(finnhub_file.read_text(encoding="utf-8"))
                news = data.get("data", {}).get("news", []) or data.get("news", [])
                if isinstance(news, list):
                    for n in news[:50]:
                        title = n.get("headline") or n.get("title") or ""
                        if title:
                            headlines.append(title)
        except Exception as exc:
            logger.debug("Failed to read Finnhub data: %s", exc)

        # Deduplicate
        seen = set()
        unique = []
        for h in headlines:
            h_clean = h.strip()
            if h_clean and h_clean not in seen:
                seen.add(h_clean)
                unique.append(h_clean)

        return unique[:200]  # Cap at 200 headlines

    def _compute_sector_scores(self, scored_headlines: List[Dict]) -> Dict[str, Dict]:
        """Compute per-sector aggregate sentiment scores."""
        sector_data: Dict[str, List[Dict]] = {}
        for item in scored_headlines:
            sectors = self._classify_sector(item["headline"])
            for sector in sectors:
                if sector not in sector_data:
                    sector_data[sector] = []
                sector_data[sector].append(item)

        results = {}
        for sector, items in sector_data.items():
            pos_scores = [i["positive_score"] for i in items]
            neg_scores = [i["negative_score"] for i in items]
            neu_scores = [i["neutral_score"] for i in items]
            n = len(items)
            results[sector] = {
                "headline_count": n,
                "avg_positive": round(sum(pos_scores) / n, 4),
                "avg_negative": round(sum(neg_scores) / n, 4),
                "avg_neutral": round(sum(neu_scores) / n, 4),
                "net_sentiment": round((sum(pos_scores) - sum(neg_scores)) / n, 4),
                "most_positive": max(items, key=lambda x: x["positive_score"])["headline"][:100] if items else "",
                "most_negative": max(items, key=lambda x: x["negative_score"])["headline"][:100] if items else "",
            }

        return results

    def fetch(self) -> Dict[str, Any]:
        """Fetch headlines, score with FinBERT, return per-sector sentiment."""
        try:
            if not self._load_model():
                return self._mark_failure("FinBERT model not available")

            headlines = self._gather_headlines()
            if not headlines:
                return self._mark_failure("No headlines available to score")

            logger.info("Scoring %d headlines with FinBERT...", len(headlines))
            scored = self._score_headlines(headlines)

            if not scored:
                return self._mark_failure("FinBERT scoring returned no results")

            sector_scores = self._compute_sector_scores(scored)

            # Overall market sentiment
            all_pos = [s["positive_score"] for s in scored]
            all_neg = [s["negative_score"] for s in scored]
            overall_sentiment = round((sum(all_pos) - sum(all_neg)) / len(scored), 4)

            payload = {
                "source": self.source,
                "source_tier": self.source_tier,
                "trust_weight": self.trust_weight,
                "timestamp_utc": utc_now_iso(),
                "fresh": True,
                "data": {
                    "headlines_scored": len(scored),
                    "overall_net_sentiment": overall_sentiment,
                    "sentiment_label": "positive" if overall_sentiment > 0.1 else ("negative" if overall_sentiment < -0.1 else "neutral"),
                    "sector_scores": sector_scores,
                    "top_negative_headlines": sorted(scored, key=lambda x: x["negative_score"], reverse=True)[:5],
                    "top_positive_headlines": sorted(scored, key=lambda x: x["positive_score"], reverse=True)[:5],
                },
            }

            # Write output
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            self._output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            return self._mark_success(payload)

        except Exception as exc:
            logger.exception("FinBERTSentimentBridge.fetch failed")
            return self._mark_failure(str(exc))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bridge = FinBERTSentimentBridge()
    result = bridge.fetch()
    print(json.dumps(result, indent=2, default=str))
