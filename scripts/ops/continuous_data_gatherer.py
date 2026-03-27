#!/usr/bin/env python3
"""Continuous Data Gatherer for Global Sentinel Quantum System.

Runs 24/7 collecting geopolitical/market data every 15 minutes and feeding
it into the quantum system via signal packets.

NOT tied to market hours — collects around the clock for overnight/weekend
geopolitical developments that move futures markets.

# ---------------------------------------------------------------------------
# systemd unit file — install as gs-data-gatherer.service
# ---------------------------------------------------------------------------
# Save the following to /etc/systemd/system/gs-data-gatherer.service
#
# [Unit]
# Description=Global Sentinel Continuous Data Gatherer
# After=network-online.target
# Wants=network-online.target
# StartLimitIntervalSec=300
# StartLimitBurst=5
#
# [Service]
# Type=simple
# User=openclaw
# WorkingDirectory=/opt/global-sentinel
# ExecStart=/usr/bin/python3 /opt/global-sentinel/scripts/ops/continuous_data_gatherer.py
# Restart=always
# RestartSec=30
# Environment=PYTHONUNBUFFERED=1
# StandardOutput=journal
# StandardError=journal
# SyslogIdentifier=gs-data-gatherer
# KillSignal=SIGTERM
# TimeoutStopSec=30
#
# [Install]
# WantedBy=multi-user.target
#
# Install commands:
#   sudo cp scripts/ops/continuous_data_gatherer.py /opt/global-sentinel/scripts/ops/
#   sudo systemctl daemon-reload
#   sudo systemctl enable gs-data-gatherer
#   sudo systemctl start gs-data-gatherer
#   journalctl -u gs-data-gatherer -f
# ---------------------------------------------------------------------------

Usage:
    python scripts/ops/continuous_data_gatherer.py
"""
from __future__ import annotations

import base64
import json
import os
import signal
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup & env loading
# ---------------------------------------------------------------------------
sys.path.insert(0, "/opt/global-sentinel")

from src.monitoring.notification_window import notifications_muted

ENV_PATH = Path("/opt/global-sentinel/.env")
REPO_ROOT = Path("/opt/global-sentinel")
RESEARCH_LOG = REPO_ROOT / "logs" / "research" / "continuous_intel.jsonl"
QUANTUM_FEED_DIR = REPO_ROOT / "data" / "quantum_feed"
LATEST_SIGNAL = QUANTUM_FEED_DIR / "latest_signal.json"

ET = timezone(timedelta(hours=-4))   # EDT
CT = timezone(timedelta(hours=-5))   # CDT
UTC = timezone.utc

COLLECTION_INTERVAL_SEC = 15 * 60  # 15 minutes

# Graceful shutdown flag
_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    print(f"[SHUTDOWN] Received signal {signum}, shutting down gracefully...")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def load_dotenv(path: Path) -> None:
    """Minimal .env loader — no dependencies."""
    if not path.exists():
        print(f"[WARN] .env not found at {path}")
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val


load_dotenv(ENV_PATH)

# API keys
SERP_API_KEY = os.getenv("SERP_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
EXA_API_KEY = os.getenv("EXA_API_KEY", "")

# Reddit OAuth
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD", "")
# Telegram (daily summary only)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_ROLE_UPDATES_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_V6_THREAD_ID = os.getenv("TELEGRAM_V6_DIGEST_THREAD_ID", "")

# ---------------------------------------------------------------------------
# Impact bucket definitions
# ---------------------------------------------------------------------------
IMPACT_BUCKETS = {
    "OIL_SUPPLY": {
        "keywords": ["hormuz", "strait", "oil supply", "crude oil", "oil production",
                      "opec", "sanctions", "barrel", "brent", "wti", "oil embargo",
                      "petroleum", "oil export", "oil import", "pipeline",
                      "oil disruption", "oil shock", "oil spike", "crude spike",
                      "supply disruption", "persian gulf", "oil blockade", "irgc navy",
                      "crack spread", "oil futures", "contango", "backwardation"],
    },
    "ENERGY_CASCADE": {
        "keywords": ["lng", "natural gas", "nat gas", "electricity", "coal",
                      "power grid", "energy crisis", "energy price", "utility",
                      "refinery", "gasoline", "diesel", "heating oil",
                      "energy disruption", "blackout", "fuel shortage", "petrol",
                      "energy security", "strategic reserve", "spr release"],
    },
    "AVIATION": {
        "keywords": ["airline", "aviation", "airspace", "flight", "airport",
                      "jet fuel", "boeing", "airbus", "faa", "no-fly zone",
                      "grounded", "travel ban", "flight cancellation",
                      "air traffic", "overflight", "airspace closure"],
    },
    "SHIPPING": {
        "keywords": ["tanker", "shipping", "freight", "container", "port",
                      "maritime", "insurance premium", "war risk", "suez",
                      "chokepoint", "vessel", "cargo", "bab el-mandeb",
                      "houthi", "red sea", "tanker rerouting", "cape route",
                      "lloyd's war risk", "marine insurance", "piracy",
                      "strait closure", "naval blockade", "mine sweeping"],
    },
    "DEFENSE": {
        "keywords": ["defense", "military", "weapons", "missile", "drone",
                      "pentagon", "nato", "troops", "army", "navy", "air force",
                      "lockheed", "raytheon", "northrop", "general dynamics",
                      "bae systems"],
    },
    "SAFE_HAVEN": {
        "keywords": ["gold", "treasury", "safe haven", "swiss franc", "yen",
                      "bond yield", "flight to safety", "risk off", "dollar",
                      "precious metal", "silver"],
    },
    "FOOD_CHAIN": {
        "keywords": ["fertilizer", "agriculture", "wheat", "corn", "food price",
                      "food supply", "grain", "farming", "crop", "livestock",
                      "food crisis"],
    },
    "INFLATION": {
        "keywords": ["inflation", "cpi", "interest rate", "fed", "federal reserve",
                      "consumer price", "cost of living", "stagflation",
                      "rate hike", "rate cut", "monetary policy"],
    },
    "TECH_SELLOFF": {
        "keywords": ["tech sell", "nasdaq", "growth stock", "tech sector",
                      "semiconductor", "chip", "ai stock", "magnificent seven",
                      "faang", "tech bubble", "valuation"],
    },
    "GEOPOLITICAL": {
        "keywords": ["escalation", "de-escalation", "ceasefire", "diplomat",
                      "negotiation", "un security", "iran", "israel",
                      "saudi", "china", "russia", "ally", "coalition",
                      "retaliatory", "nuclear"],
    },
}

# ---------------------------------------------------------------------------
# Search queries for SerpAPI
# ---------------------------------------------------------------------------
SERP_QUERIES = [
    "Iran war latest",
    "Strait of Hormuz shipping disruption",
    "oil price surge crude",
    "airline fuel costs jet fuel",
    "defense spending military contract",
    "gold safe haven demand",
    "tanker rates war risk premium",
    "Iran oil supply disruption sanctions",
    "energy crisis natural gas LNG",
    "oil inflation CPI impact",
    "Houthi Red Sea shipping attack",
    "Iran IRGC navy Persian Gulf",
    "oil refining crack spread margins",
    "fertilizer food prices oil impact",
]

# Reddit subreddits
REDDIT_SUBS = [
    "wallstreetbets",
    "geopolitics",
    "energy",
    "shipping",
]

# Yahoo Finance symbols
YAHOO_SYMBOLS = [
    ("CL=F", "WTI Crude"),
    ("BZ=F", "Brent Crude"),
    ("NG=F", "Natural Gas"),
    ("RB=F", "Gasoline RBOB"),
    ("GC=F", "Gold"),
    ("^VIX", "VIX"),
    ("ES=F", "S&P Futures"),
    ("NQ=F", "Nasdaq Futures"),
]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    raw: bool = False,
) -> Any:
    """GET JSON from url, return parsed dict or None on error."""
    hdrs = {"Accept": "application/json", "User-Agent": "GlobalSentinel/1.0"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
            data = resp.read().decode("utf-8")
            if raw:
                return data
            return json.loads(data)
    except Exception as exc:
        print(f"  [HTTP ERR] {url[:90]}... => {exc}")
        return None


def send_telegram(text: str, bot_token: str, chat_id: str,
                  thread_id: Optional[str] = None) -> bool:
    """Send a Telegram message using urllib (no requests dependency)."""
    if notifications_muted():
        print("  [TG] Automated updates muted, skipping send")
        return False
    if not bot_token or not chat_id:
        return False
    text = text[:4096]
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if thread_id:
        try:
            payload["message_thread_id"] = int(thread_id)
        except (ValueError, TypeError):
            pass
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("ok", False)
    except Exception as exc:
        print(f"  [TG] Send failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Data source fetchers
# ---------------------------------------------------------------------------

def fetch_serp_news() -> List[Dict[str, Any]]:
    """Fetch news via Exa AI search (replaced SerpAPI 2026-03-23)."""
    if not EXA_API_KEY:
        print("  [EXA] No EXA_API_KEY set, skipping")
        return []

    all_results: List[Dict[str, Any]] = []
    exa_queries = [
        "Iran war latest geopolitical",
        "Strait of Hormuz oil shipping disruption",
        "crude oil price surge supply shock",
        "defense military spending contract",
        "Houthi Red Sea shipping attack",
    ]
    headers = {
        "x-api-key": EXA_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    for query in exa_queries:
        if _shutdown:
            break
        try:
            payload = json.dumps({
                "query": query,
                "type": "neural",
                "useAutoprompt": True,
                "numResults": 10,
                "contents": {"text": {"maxCharacters": 300}},
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.exa.ai/search",
                data=payload,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for result in (data.get("results") or []):
                all_results.append({
                    "source": "exa_search",
                    "title": result.get("title", ""),
                    "url": result.get("url", ""),
                    "snippet": (result.get("text") or "")[:300],
                    "published": result.get("publishedDate", ""),
                    "score": result.get("score", 0),
                })
            print(f"  [EXA] \'{query[:40]}\' => {len(data.get('results', []))} results")
        except Exception as exc:
            print(f"  [EXA] \'{query[:40]}\' failed: {exc}")
        time.sleep(0.5)
    print(f"  [EXA] Total => {len(all_results)} articles")
    return all_results
    if not SERP_API_KEY:
        print("  [SERP] No API key, skipping")
        return []

    all_results = []
    for query in SERP_QUERIES:
        if _shutdown:
            break
        encoded = urllib.parse.quote(query)
        url = f"https://serpapi.com/search.json?engine=google_news&q={encoded}&api_key={SERP_API_KEY}"
        data = http_get(url, timeout=20)
        if not data:
            continue

        articles = data.get("news_results", []) or data.get("organic_results", [])
        for art in articles[:10]:
            item = {
                "source": "serp_google_news",
                "query": query,
                "title": art.get("title", ""),
                "link": art.get("link", ""),
                "snippet": art.get("snippet", art.get("description", ""))[:300],
                "date": art.get("date", ""),
            }
            all_results.append(item)
        print(f"  [SERP] '{query}' => {len(articles)} results")
        time.sleep(0.5)

    return all_results


# GDELT 30-minute cache
_gdelt_cache: Dict[str, Any] = {"ts": 0.0, "data": []}
_GDELT_CACHE_TTL = 1800  # 30 minutes

def fetch_gdelt() -> List[Dict[str, Any]]:
    """Fetch articles from GDELT DOC API with exponential backoff and 30min cache."""
    now = time.time()
    if _gdelt_cache["data"] and (now - _gdelt_cache["ts"]) < _GDELT_CACHE_TTL:
        print(f"  [GDELT] Returning cached {len(_gdelt_cache['data'])} articles (age {int(now - _gdelt_cache['ts'])}s)")
        return list(_gdelt_cache["data"])

    url = "https://api.gdeltproject.org/api/v2/doc/doc?query=iran%20war%20oil&mode=ArtList&maxrecords=20&format=json"
    data = None
    max_retries = 4
    for attempt in range(max_retries):
        data = http_get(url, timeout=20)
        if data:
            break
        backoff = min(10 * (2 ** attempt), 120)  # 10s, 20s, 40s, 80s
        print(f"  [GDELT] Attempt {attempt + 1}/{max_retries} failed, backing off {backoff}s...")
        time.sleep(backoff)

    if not data:
        print("  [GDELT] All retries exhausted, returning stale cache or empty")
        return list(_gdelt_cache["data"])

    articles = data.get("articles", [])
    results = []
    for art in articles:
        item = {
            "source": "gdelt",
            "title": art.get("title", ""),
            "url": art.get("url", ""),
            "seendate": art.get("seendate", ""),
            "domain": art.get("domain", ""),
            "tone": art.get("tone", 0),
        }
        results.append(item)
    _gdelt_cache["ts"] = now
    _gdelt_cache["data"] = list(results)
    print(f"  [GDELT] => {len(results)} articles (cached for {_GDELT_CACHE_TTL}s)")
    return results


def fetch_yahoo_quotes() -> Dict[str, Dict[str, Any]]:
    """Fetch real-time quotes from Yahoo Finance."""
    quotes = {}
    for symbol, name in YAHOO_SYMBOLS:
        if _shutdown:
            break
        encoded = urllib.parse.quote(symbol)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1m&range=1d"
        data = http_get(url)
        if not data:
            url2 = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range=5d"
            data = http_get(url2)
        if data:
            result = data.get("chart", {}).get("result", [])
            if result:
                meta = result[0].get("meta", {})
                price = meta.get("regularMarketPrice", 0)
                prev_close = meta.get("previousClose") or meta.get("chartPreviousClose", 0)
                change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
                quotes[symbol] = {
                    "name": name,
                    "price": price,
                    "prev_close": prev_close,
                    "change_pct": round(change_pct, 2),
                }
                print(f"  [YAHOO] {name}: ${price:,.2f} ({change_pct:+.2f}%)")
            else:
                print(f"  [YAHOO] {name}: no result data")
        time.sleep(0.3)

    return quotes



def _get_reddit_oauth_token():
    """Get Reddit OAuth token using script-type app credentials."""
    client_id = os.getenv("REDDIT_CLIENT_ID", "")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
    username = os.getenv("REDDIT_USERNAME", "")
    password = os.getenv("REDDIT_PASSWORD", "")
    if not all([client_id, client_secret, username, password]):
        return None
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "password",
        "username": username,
        "password": password,
    }).encode()
    req = urllib.request.Request("https://www.reddit.com/api/v1/access_token",
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "User-Agent": "GlobalSentinel/1.0",
        })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("access_token")
    except Exception as e:
        print(f"  [REDDIT OAuth] Token error: {e}")
        return None


def _fetch_reddit_oauth(subreddit, token, limit=10):
    """Fetch subreddit posts via Reddit OAuth API."""
    url = f"https://oauth.reddit.com/r/{subreddit}/hot?limit={limit}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "GlobalSentinel/1.0",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return [child["data"] for child in data["data"]["children"]]

def fetch_reddit() -> List[Dict[str, Any]]:
    """Fetch hot posts from relevant subreddits. Falls back to SerpAPI if blocked."""
    all_results = []
    blocked = False
    for sub in REDDIT_SUBS:
        if _shutdown:
            break
        url = f"https://api.pullpush.io/reddit/search/submission/?subreddit={sub}&size=10&sort=desc&sort_type=score"
        data = http_get(url, timeout=20)
        if not data or "data" not in data:
            blocked = True
            continue

        children = data.get("data", [])
        for d in children:
            if d.get("stickied"):
                continue
            item = {
                "source": "reddit",
                "subreddit": sub,
                "title": d.get("title", ""),
                "score": d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "url": d.get("url", ""),
                "selftext": (d.get("selftext") or "")[:200],
                "created_utc": d.get("created_utc", 0),
            }
            all_results.append(item)
        print(f"  [REDDIT] r/{sub} => {len(children)} posts (via PullPush)")
        time.sleep(1.0)

    # Fallback: SerpAPI Reddit search if direct access blocked
    if blocked and not all_results and SERP_API_KEY:
        print("  [REDDIT] PullPush unavailable — falling back to SerpAPI Reddit search")
        reddit_queries = [
            "Iran war oil site:reddit.com",
            "Hormuz shipping disruption site:reddit.com",
            "oil stocks trading site:reddit.com/r/wallstreetbets",
        ]
        for q in reddit_queries:
            if _shutdown:
                break
            params = urllib.parse.urlencode({
                "q": q, "api_key": SERP_API_KEY,
                "engine": "google", "num": 10,
            })
            data = http_get(f"https://serpapi.com/search.json?{params}")
            if not data:
                continue
            for r in data.get("organic_results", []):
                item = {
                    "source": "reddit_serp",
                    "title": r.get("title", ""),
                    "snippet": r.get("snippet", "")[:200],
                    "url": r.get("link", ""),
                    "score": 0,
                }
                all_results.append(item)
            count = len(data.get("organic_results", []))
            print(f"  [REDDIT-SERP] '{q[:40]}...' => {count} results")
            time.sleep(0.5)

    return all_results


def fetch_finnhub_news() -> List[Dict[str, Any]]:
    """Fetch general news from Finnhub."""
    if not FINNHUB_API_KEY:
        print("  [FINNHUB] No API key, skipping")
        return []

    url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}"
    data = http_get(url)
    if not data or not isinstance(data, list):
        return []

    results = []
    for art in data[:30]:
        item = {
            "source": "finnhub",
            "title": art.get("headline", ""),
            "summary": (art.get("summary") or "")[:300],
            "news_source": art.get("source", ""),
            "datetime": art.get("datetime", 0),
            "url": art.get("url", ""),
        }
        results.append(item)
    print(f"  [FINNHUB] => {len(results)} articles")
    return results


# ---------------------------------------------------------------------------
# Intelligence analysis

def fetch_google_news_rss() -> List[Dict[str, Any]]:
    """Fetch news from Google News RSS feeds (free, no API key, no rate limits)."""
    import xml.etree.ElementTree as ET_xml
    
    rss_queries = [
        "iran+war", "oil+price+surge", "strait+of+hormuz",
        "energy+crisis", "oil+supply+disruption", "gold+safe+haven",
        "defense+stocks", "shipping+disruption", "inflation+oil",
        "geopolitical+risk",
    ]
    all_results = []
    
    for query in rss_queries:
        if _shutdown:
            break
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; GlobalSentinel/5.1)"
            })
            with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
                xml_data = resp.read().decode("utf-8")
            
            root = ET_xml.fromstring(xml_data)
            items = root.findall(".//item")
            for item in items[:8]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub_date = (item.findtext("pubDate") or "").strip()
                source_el = item.find("source")
                source_name = source_el.text if source_el is not None else ""
                
                if title:
                    all_results.append({
                        "source": "google_news_rss",
                        "query": query.replace("+", " "),
                        "title": title,
                        "link": link,
                        "date": pub_date,
                        "publisher": source_name,
                    })
            count = min(len(items), 8)
            if count > 0:
                print(f"  [GNEWS RSS] \'{query.replace('+', ' ')}\' => {count} results")
        except Exception as exc:
            print(f"  [GNEWS RSS] \'{query.replace('+', ' ')}\' failed: {exc}")
        time.sleep(0.3)  # gentle rate limiting
    
    print(f"  [GNEWS RSS] Total: {len(all_results)} articles")
    return all_results


# ---------------------------------------------------------------------------

def classify_item(text: str) -> List[Tuple[str, str]]:
    """Classify a text item into impact buckets. Returns list of (bucket, matched_keyword)."""
    text_lower = text.lower()
    matches = []
    for bucket_name, bucket_info in IMPACT_BUCKETS.items():
        for kw in bucket_info["keywords"]:
            if kw in text_lower:
                matches.append((bucket_name, kw))
                break  # One match per bucket is enough
    return matches


def analyze_intelligence(
    serp_news: List[Dict],
    reddit_posts: List[Dict],
    finnhub_news: List[Dict],
    gdelt_articles: List[Dict],
    google_rss_news: Optional[List[Dict]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Score all impact buckets based on intelligence gathered."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for name in IMPACT_BUCKETS:
        buckets[name] = {"score": 0, "signals": [], "count": 0}

    # Process all items — deduplicate by title
    all_items: List[Tuple[str, str, Dict]] = []
    seen_titles: set = set()

    for item in serp_news:
        title = item.get("title", "").strip().lower()[:80]
        if title and title not in seen_titles:
            seen_titles.add(title)
            all_items.append(("SERP", item.get("title", "") + " " + item.get("snippet", ""), item))

    for item in reddit_posts:
        title = item.get("title", "").strip().lower()[:80]
        if title and title not in seen_titles:
            seen_titles.add(title)
            all_items.append(("REDDIT", item.get("title", "") + " " + item.get("selftext", ""), item))

    for item in finnhub_news:
        title = item.get("title", "").strip().lower()[:80]
        if title and title not in seen_titles:
            seen_titles.add(title)
            all_items.append(("FINNHUB", item.get("title", "") + " " + item.get("summary", ""), item))

    for item in gdelt_articles:
        title = item.get("title", "").strip().lower()[:80]
        if title and title not in seen_titles:
            seen_titles.add(title)
            all_items.append(("GDELT", item.get("title", ""), item))

    for item in (google_rss_news or []):
        title = item.get("title", "").strip().lower()[:80]
        if title and title not in seen_titles:
            seen_titles.add(title)
            all_items.append(("GNEWS", item.get("title", ""), item))

    for source_tag, text, item in all_items:
        matches = classify_item(text)
        for bucket_name, keyword in matches:
            buckets[bucket_name]["count"] += 1
            title = item.get("title", item.get("headline", ""))[:80]
            signal_entry = f"[{source_tag}] {title}"
            if len(buckets[bucket_name]["signals"]) < 5:
                buckets[bucket_name]["signals"].append(signal_entry)

    # Compute scores (0-10) based on signal count
    for name, info in buckets.items():
        count = info["count"]
        if count == 0:
            info["score"] = 0
        elif count <= 2:
            info["score"] = 2
        elif count <= 5:
            info["score"] = 4
        elif count <= 10:
            info["score"] = 6
        elif count <= 20:
            info["score"] = 8
        else:
            info["score"] = 10

    return buckets


def compute_war_intensity(buckets: Dict[str, Dict[str, Any]]) -> float:
    """Compute overall war intensity score (0-10) from bucket scores."""
    # Weighted average — oil/geopolitical/defense matter most
    weights = {
        "OIL_SUPPLY": 2.0,
        "ENERGY_CASCADE": 1.5,
        "SHIPPING": 1.5,
        "DEFENSE": 1.5,
        "GEOPOLITICAL": 2.0,
        "AVIATION": 1.0,
        "SAFE_HAVEN": 1.0,
        "FOOD_CHAIN": 0.5,
        "INFLATION": 1.0,
        "TECH_SELLOFF": 0.5,
    }
    total_weight = 0.0
    weighted_sum = 0.0
    for name, info in buckets.items():
        w = weights.get(name, 1.0)
        weighted_sum += info["score"] * w
        total_weight += w
    if total_weight == 0:
        return 0.0
    return round(weighted_sum / total_weight, 2)


# ---------------------------------------------------------------------------
# Quantum feed writer
# ---------------------------------------------------------------------------

def write_quantum_signal(
    buckets: Dict[str, Dict[str, Any]],
    yahoo_quotes: Dict[str, Dict[str, Any]],
    war_intensity: float,
    article_counts: Dict[str, int],
) -> None:
    """Write the latest signal packet for the quantum system."""
    QUANTUM_FEED_DIR.mkdir(parents=True, exist_ok=True)

    # Build market_data summary
    market_data: Dict[str, Any] = {}
    symbol_map = {
        "CL=F": "oil_wti",
        "BZ=F": "oil_brent",
        "NG=F": "natgas",
        "RB=F": "gasoline",
        "GC=F": "gold",
        "^VIX": "vix",
        "ES=F": "sp500_futures",
        "NQ=F": "nasdaq_futures",
    }
    for sym, key in symbol_map.items():
        if sym in yahoo_quotes:
            q = yahoo_quotes[sym]
            market_data[key] = {
                "price": q["price"],
                "change_pct": q["change_pct"],
            }

    # Build bucket_scores (just name -> score)
    bucket_scores = {name: info["score"] for name, info in buckets.items()}

    # Build top_signals (top 5 per bucket)
    top_signals = {name: info["signals"][:5] for name, info in buckets.items() if info["signals"]}

    signal_packet = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "bucket_scores": bucket_scores,
        "market_data": market_data,
        "war_intensity": war_intensity,
        "article_count": article_counts,
        "top_signals": top_signals,
    }

    # Write atomically (write to tmp then rename)
    tmp_path = LATEST_SIGNAL.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        json.dump(signal_packet, f, indent=2)
    tmp_path.rename(LATEST_SIGNAL)
    print(f"  [QUANTUM] Signal written to {LATEST_SIGNAL}")


# ---------------------------------------------------------------------------
# JSONL logger
# ---------------------------------------------------------------------------

def log_cycle(
    cycle_num: int,
    serp_news: List[Dict],
    reddit_posts: List[Dict],
    finnhub_news: List[Dict],
    gdelt_articles: List[Dict],
    yahoo_quotes: Dict[str, Dict[str, Any]],
    buckets: Dict[str, Dict[str, Any]],
    war_intensity: float,
) -> None:
    """Append one JSON line per collection cycle to the research log."""
    RESEARCH_LOG.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "cycle": cycle_num,
        "raw_counts": {
            "serp": len(serp_news),
            "reddit": len(reddit_posts),
            "finnhub": len(finnhub_news),
            "gdelt": len(gdelt_articles),
        },
        "yahoo_quotes": yahoo_quotes,
        "bucket_scores": {name: info["score"] for name, info in buckets.items()},
        "war_intensity": war_intensity,
        "serp_news": serp_news[:50],        # cap raw data to keep lines reasonable
        "reddit_posts": reddit_posts[:30],
        "finnhub_news": finnhub_news[:30],
        "gdelt_articles": gdelt_articles[:20],
    }

    with open(RESEARCH_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    print(f"  [LOG] Cycle {cycle_num} appended to {RESEARCH_LOG}")


# ---------------------------------------------------------------------------
# Daily Telegram summary (once at 6 AM ET)
# ---------------------------------------------------------------------------

_last_daily_summary_date: Optional[str] = None


def maybe_send_daily_summary(
    buckets: Dict[str, Dict[str, Any]],
    yahoo_quotes: Dict[str, Dict[str, Any]],
    war_intensity: float,
    cycle_num: int,
) -> None:
    """Send one daily summary at 6 AM ET. No other Telegram messages."""
    global _last_daily_summary_date

    now_et = datetime.now(ET)
    today_str = now_et.strftime("%Y-%m-%d")

    # Only send between 6:00-6:14 AM ET, once per day
    if now_et.hour != 6 or now_et.minute >= 15:
        return
    if _last_daily_summary_date == today_str:
        return

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    _last_daily_summary_date = today_str

    # Build summary text
    lines = [f"GS Data Gatherer - Daily Summary ({today_str})"]
    lines.append(f"War Intensity: {war_intensity}/10 | Cycle: #{cycle_num}")
    lines.append("")

    # Top buckets
    sorted_buckets = sorted(buckets.items(), key=lambda x: x[1]["score"], reverse=True)
    for name, info in sorted_buckets[:5]:
        if info["score"] > 0:
            lines.append(f"  {name}: {info['score']}/10 ({info['count']} signals)")

    # Key market prices
    lines.append("")
    for sym, key_label in [("CL=F", "WTI"), ("GC=F", "Gold"), ("^VIX", "VIX"),
                            ("ES=F", "ES"), ("NG=F", "NatGas")]:
        if sym in yahoo_quotes:
            q = yahoo_quotes[sym]
            lines.append(f"  {key_label}: ${q['price']:,.2f} ({q['change_pct']:+.2f}%)")

    text = "\n".join(lines)

    if TELEGRAM_V6_THREAD_ID:
        send_telegram(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_V6_THREAD_ID)
    else:
        send_telegram(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    print(f"  [TG] Daily summary sent for {today_str}")


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------

def run_collection_cycle(cycle_num: int) -> None:
    """Run one full data collection + analysis + quantum feed cycle."""
    now = datetime.now(UTC)
    print(f"\n{'='*70}")
    print(f"[CYCLE {cycle_num}] Starting at {now.isoformat()} UTC")
    print(f"{'='*70}")

    # --- Collect from all sources (each tolerant of failure) ---
    serp_news: List[Dict] = []
    reddit_posts: List[Dict] = []
    finnhub_news: List[Dict] = []
    gdelt_articles: List[Dict] = []
    yahoo_quotes: Dict[str, Dict[str, Any]] = {}

    try:
        print("[1/6] Fetching SerpAPI news...")
        serp_news = fetch_serp_news()
    except Exception as exc:
        print(f"  [ERR] SerpAPI failed: {exc}")
        traceback.print_exc()

    if _shutdown:
        return

    try:
        print("[2/6] Fetching GDELT articles...")
        gdelt_articles = fetch_gdelt()
    except Exception as exc:
        print(f"  [ERR] GDELT failed: {exc}")
        traceback.print_exc()

    if _shutdown:
        return

    try:
        print("[3/6] Fetching Yahoo Finance quotes...")
        yahoo_quotes = fetch_yahoo_quotes()
    except Exception as exc:
        print(f"  [ERR] Yahoo Finance failed: {exc}")
        traceback.print_exc()

    if _shutdown:
        return

    try:
        print("[4/6] Fetching Reddit posts...")
        reddit_posts = fetch_reddit()
    except Exception as exc:
        print(f"  [ERR] Reddit failed: {exc}")
        traceback.print_exc()

    if _shutdown:
        return

    try:
        print("[5/6] Fetching Finnhub news...")
        finnhub_news = fetch_finnhub_news()
    except Exception as exc:
        print(f"  [ERR] Finnhub failed: {exc}")
        traceback.print_exc()

    if _shutdown:
        return

    google_rss_news: List[Dict] = []
    try:
        print("[6/6] Fetching Google News RSS...")
        google_rss_news = fetch_google_news_rss()
    except Exception as exc:
        print(f"  [ERR] Google News RSS failed: {exc}")
        traceback.print_exc()

    # --- Analyze ---
    print("\n[ANALYSIS] Classifying intelligence into buckets...")
    buckets = analyze_intelligence(serp_news, reddit_posts, finnhub_news, gdelt_articles, google_rss_news)
    war_intensity = compute_war_intensity(buckets)

    article_counts = {
        "serp": len(serp_news),
        "reddit": len(reddit_posts),
        "finnhub": len(finnhub_news),
        "gdelt": len(gdelt_articles),
        "google_rss": len(google_rss_news),
        "total": len(serp_news) + len(reddit_posts) + len(finnhub_news) + len(gdelt_articles) + len(google_rss_news),
    }

    # Print bucket summary
    print(f"\n  War Intensity: {war_intensity}/10  |  Total articles: {article_counts['total']}")
    for name, info in sorted(buckets.items(), key=lambda x: x[1]["score"], reverse=True):
        if info["score"] > 0:
            print(f"    {name:20s}: {info['score']:2d}/10  ({info['count']:3d} signals)")

    # --- Write quantum feed ---
    try:
        write_quantum_signal(buckets, yahoo_quotes, war_intensity, article_counts)
    except Exception as exc:
        print(f"  [ERR] Quantum feed write failed: {exc}")
        traceback.print_exc()

    # --- Log to JSONL ---
    try:
        log_cycle(cycle_num, serp_news, reddit_posts, finnhub_news,
                  gdelt_articles, yahoo_quotes, buckets, war_intensity)
    except Exception as exc:
        print(f"  [ERR] JSONL log failed: {exc}")
        traceback.print_exc()

    # --- Daily Telegram summary (6 AM ET only) ---
    try:
        maybe_send_daily_summary(buckets, yahoo_quotes, war_intensity, cycle_num)
    except Exception as exc:
        print(f"  [ERR] Telegram summary failed: {exc}")
        traceback.print_exc()

    elapsed = (datetime.now(UTC) - now).total_seconds()
    print(f"\n[CYCLE {cycle_num}] Completed in {elapsed:.1f}s")


def main() -> None:
    """Main entry point — runs forever until SIGTERM."""
    print("=" * 70)
    print("  Global Sentinel — Continuous Data Gatherer")
    print(f"  Started at {datetime.now(UTC).isoformat()} UTC")
    print(f"  Collection interval: {COLLECTION_INTERVAL_SEC}s ({COLLECTION_INTERVAL_SEC // 60} min)")
    print(f"  SERP_API_KEY: {'SET' if SERP_API_KEY else 'MISSING'}")
    print(f"  FINNHUB_API_KEY: {'SET' if FINNHUB_API_KEY else 'MISSING'}")
    print(f"  Quantum feed: {LATEST_SIGNAL}")
    print(f"  Research log: {RESEARCH_LOG}")
    print("=" * 70)

    # Ensure directories exist
    RESEARCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    QUANTUM_FEED_DIR.mkdir(parents=True, exist_ok=True)

    cycle_num = 0

    while not _shutdown:
        cycle_num += 1
        try:
            run_collection_cycle(cycle_num)
        except Exception as exc:
            print(f"\n[FATAL] Cycle {cycle_num} crashed (non-fatal, will retry): {exc}")
            traceback.print_exc()

        if _shutdown:
            break

        # Sleep in small increments so SIGTERM is responsive
        print(f"\n[SLEEP] Next cycle in {COLLECTION_INTERVAL_SEC // 60} minutes...")
        sleep_remaining = COLLECTION_INTERVAL_SEC
        while sleep_remaining > 0 and not _shutdown:
            chunk = min(sleep_remaining, 5)
            time.sleep(chunk)
            sleep_remaining -= chunk

    print(f"\n[SHUTDOWN] Exiting after {cycle_num} cycles. Goodbye.")


if __name__ == "__main__":
    main()
