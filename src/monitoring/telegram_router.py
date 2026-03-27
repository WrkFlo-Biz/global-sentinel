"""Centralized Telegram topic router for all GS notifications."""
import json, os, urllib.request
from pathlib import Path

# Load .env if not already in environment
_REPO = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
_env_path = _REPO / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
            if _k not in os.environ or not os.environ[_k]:
                os.environ[_k] = _v

CHAT_ID = os.getenv("TELEGRAM_TOPIC_CHAT_ID", "-1003898688720")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Topic routing map
TOPICS = {
    "trading": os.getenv("TELEGRAM_TRADING_THREAD_ID", ""),
    "macro": os.getenv("TELEGRAM_MACRO_THREAD_ID", ""),
    "performance": os.getenv("TELEGRAM_PERFORMANCE_THREAD_ID", ""),
    "congress": os.getenv("TELEGRAM_CONGRESS_THREAD_ID", ""),
    "system": os.getenv("TELEGRAM_SYSTEM_THREAD_ID", ""),
    "research": os.getenv("TELEGRAM_RESEARCH_THREAD_ID", "17"),
    "canary": os.getenv("TELEGRAM_CANARY_THREAD_ID", "15"),
    "advisories": os.getenv("TELEGRAM_ADVISORIES_THREAD_ID", "18"),
    "digest": os.getenv("TELEGRAM_V6_DIGEST_THREAD_ID", "74"),
}

def send(msg, topic="digest"):
    """Send message to a specific topic thread."""
    try:
        if not TOKEN: return
        thread_id = TOPICS.get(topic, TOPICS["digest"])
        payload = {"chat_id": CHAT_ID, "text": msg[:4000], "parse_mode": "HTML"}
        # ALWAYS include thread_id -- never send to main channel
        thread_id = thread_id or TOPICS.get("digest", "74") or "74"
        if True:
            payload["message_thread_id"] = int(thread_id)
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram send error ({topic}): {e}")
