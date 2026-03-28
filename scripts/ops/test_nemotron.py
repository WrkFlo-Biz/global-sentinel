#!/usr/bin/env python3
"""Test NVIDIA Nemotron via OpenRouter API.

Usage:
    python3 test_nemotron.py

Requires OPENROUTER_API_KEY in environment or /opt/global-sentinel/.env
"""

import os
import sys
import json

# Try to load from .env file if env var not set
def load_env():
    env_path = "/opt/global-sentinel/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    if key.strip() not in os.environ:
                        os.environ[key.strip()] = value.strip()

load_env()

API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = os.environ.get("NEMOTRON_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
API_BASE = os.environ.get("NEMOTRON_API_BASE", "https://openrouter.ai/api/v1")

def test_nemotron():
    if not API_KEY or API_KEY == "PENDING_SETUP":
        print("=" * 60)
        print("OPENROUTER_API_KEY not set or pending setup.")
        print()
        print("To get a free API key:")
        print("  1. Go to https://openrouter.ai")
        print("  2. Sign up / log in")
        print("  3. Go to https://openrouter.ai/keys")
        print("  4. Create a new key")
        print("  5. Add to /opt/global-sentinel/.env:")
        print("     OPENROUTER_API_KEY=sk-or-v1-xxxxx")
        print()
        print("Free Nemotron models available:")
        print("  - nvidia/nemotron-3-super-120b-a12b:free")
        print("  - nvidia/nemotron-3-nano-30b:free")
        print("  - nvidia/nemotron-nano-12b:free")
        print("=" * 60)
        return False

    try:
        import urllib.request
        import urllib.error

        url = f"{API_BASE}/chat/completions"
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "user", "content": "What is the current market regime? Answer in 2-3 sentences."}
            ],
            "max_tokens": 200,
            "temperature": 0.7,
        }

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Wrk-Flo/global-sentinel",
            "X-Title": "Global Sentinel Trading System",
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        print(f"Testing model: {MODEL}")
        print(f"API base: {API_BASE}")
        print(f"Prompt: 'What is the current market regime?'")
        print("-" * 60)

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        content = result["choices"][0]["message"]["content"]
        model_used = result.get("model", MODEL)
        usage = result.get("usage", {})

        print(f"Model: {model_used}")
        print(f"Tokens: {usage.get('prompt_tokens', '?')} in / {usage.get('completion_tokens', '?')} out")
        print(f"Response:\n{content}")
        print("-" * 60)
        print("SUCCESS: Nemotron is working.")
        return True

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP Error {e.code}: {e.reason}")
        print(f"Body: {body}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


if __name__ == "__main__":
    success = test_nemotron()
    sys.exit(0 if success else 1)
