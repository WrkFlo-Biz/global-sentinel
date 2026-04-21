#!/usr/bin/env python3
"""
Unified Web Search Bridge — Cascading fallback across all free search providers.
Replaces Exa/SerpAPI when out of credits.

Cascade order:
1. DuckDuckGo Search (pip, free unlimited)
2. Google News RSS (free unlimited, best for news)
3. Finnhub News (60 req/min, financial-specific)
4. Brave Search (limited free credits)
5. OpenClaw bot proxy (LLM-powered web research via Telegram bot)
6. GDELT (geopolitical events, sometimes slow)
"""
import json, os, datetime, urllib.request, urllib.error, time
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
OUTPUT_PATH = REPO_ROOT / "data/quantum_feed/web_search_results.json"

env = {}
env_path = REPO_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def log(msg):
    print(f"[{iso_now()}] SEARCH: {msg}", flush=True)

def search_ddg(query, max_results=10):
    """DuckDuckGo Search — free, unlimited, best general search."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [{"title": r.get("title",""), "url": r.get("href",""), "snippet": r.get("body",""), "source": "duckduckgo"} for r in results]
    except Exception as e:
        log(f"DDG failed: {e}")
        return []

def search_ddg_news(query, max_results=10):
    """DuckDuckGo News — free, unlimited, news-specific."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
        return [{"title": r.get("title",""), "url": r.get("url",""), "snippet": r.get("body",""),
                 "date": r.get("date",""), "source": "duckduckgo_news"} for r in results]
    except Exception as e:
        log(f"DDG News failed: {e}")
        return []

def search_google_rss(query, max_results=20):
    """Google News RSS — free, unlimited, excellent for financial news."""
    try:
        import xml.etree.ElementTree as ET
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
        data = urllib.request.urlopen(url, timeout=15).read()
        root = ET.fromstring(data)
        items = root.findall(".//item")
        return [{"title": item.find("title").text or "", "url": item.find("link").text or "",
                 "date": item.find("pubDate").text or "", "source": "google_news_rss"} for item in items[:max_results]]
    except Exception as e:
        log(f"Google RSS failed: {e}")
        return []

def search_finnhub(symbol, days=7):
    """Finnhub Company News — 60 req/min, financial-specific."""
    try:
        key = env.get("FINNHUB_API_KEY", "")
        if not key: return []
        from datetime import date, timedelta
        today = date.today().isoformat()
        start = (date.today() - timedelta(days=days)).isoformat()
        url = f"https://finnhub.io/api/v1/company-news?symbol={symbol}&from={start}&to={today}&token={key}"
        data = json.loads(urllib.request.urlopen(url, timeout=10).read())
        return [{"title": a.get("headline",""), "url": a.get("url",""), "snippet": a.get("summary","")[:200],
                 "date": a.get("datetime",""), "source": "finnhub"} for a in data[:20]]
    except Exception as e:
        log(f"Finnhub failed: {e}")
        return []

def search_brave(query, max_results=5):
    """Brave Search — limited free credits, good quality."""
    try:
        key = env.get("BRAVE_API_KEY", "")
        if not key: return []
        url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={max_results}"
        req = urllib.request.Request(url)
        req.add_header("X-Subscription-Token", key)
        req.add_header("Accept", "application/json")
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return [{"title": r.get("title",""), "url": r.get("url",""), "snippet": r.get("description",""),
                 "source": "brave"} for r in data.get("web",{}).get("results",[])]
    except Exception as e:
        log(f"Brave failed: {e}")
        return []

def search_openclaw(query):
    """Proxy search through OpenClaw bot via gateway API."""
    try:
        gateway_url = "http://localhost:18789"
        # OpenClaw gateway can execute web searches if configured
        # This is a placeholder — needs the gateway to expose a search endpoint
        return []
    except:
        return []

def unified_search(query, search_type="general", symbol=None, max_results=15):
    """
    Cascade through all search providers until we get results.
    Returns combined, deduplicated results.
    """
    all_results = []

    if search_type == "news" or search_type == "general":
        # 1. DuckDuckGo News (best for real-time)
        results = search_ddg_news(query, max_results)
        all_results.extend(results)
        if len(all_results) >= max_results:
            log(f"DDG News: {len(results)} results (sufficient)")
            return all_results[:max_results]

        # 2. Google News RSS (excellent backup)
        results = search_google_rss(query, max_results)
        all_results.extend(results)
        if len(all_results) >= max_results:
            log(f"+ Google RSS: {len(results)} results (sufficient)")
            return all_results[:max_results]

    if symbol:
        # 3. Finnhub (financial-specific)
        results = search_finnhub(symbol)
        all_results.extend(results)
        log(f"+ Finnhub: {len(results)} results")

    if search_type == "general":
        # 4. DuckDuckGo Web (general search)
        results = search_ddg(query, max_results)
        all_results.extend(results)
        log(f"+ DDG Web: {len(results)} results")

    # 5. Brave (if others failed)
    if len(all_results) < 5:
        results = search_brave(query, 5)
        all_results.extend(results)
        log(f"+ Brave: {len(results)} results")

    # Deduplicate by URL
    seen = set()
    unique = []
    for r in all_results:
        url = r.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(r)

    log(f"Total: {len(unique)} unique results for {query}")
    return unique[:max_results]

def poll():
    """Bridge poll function — searches for top watchlist stocks."""
    watchlist = ["SPY", "NVDA", "TSLA", "META", "AMD", "XLE", "USO"]
    results = {}

    for sym in watchlist:
        query = f"{sym} stock news today"
        articles = unified_search(query, search_type="news", symbol=sym, max_results=5)
        results[sym] = articles
        time.sleep(1)  # Rate limit

    output = {"timestamp": iso_now(), "results": results, "total_articles": sum(len(v) for v in results.values())}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    return output

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        results = unified_search(query, search_type="news", max_results=10)
        for r in results:
            print(f"[{r['source']}] {r['title'][:80]}")
    else:
        output = poll()
        print(f"Polled {len(output[results])} symbols, {output[total_articles]} total articles")
