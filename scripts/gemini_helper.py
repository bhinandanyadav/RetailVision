"""Simple helper to call Google Generative Language (Gemini) via REST.

Usage:
  python scripts/gemini_helper.py --prompt "Explain AI in a few words"

Provide the API key via the `GOOGLE_API_KEY` environment variable or `--api-key`.
This script sends a POST to the same endpoint used in the provided cURL.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import requests


DEFAULT_MODEL = "gemini-flash-latest"


def generate_content(api_key: str, prompt: str, model: str = DEFAULT_MODEL, timeout: int = 60) -> Any:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": api_key,
    }
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, resp.text


def main() -> int:
    p = argparse.ArgumentParser(description="Call Gemini generateContent endpoint")
    p.add_argument("--prompt", "-p", required=True, help="Prompt text to send")
    p.add_argument("--model", "-m", default=DEFAULT_MODEL, help="Model name")
    p.add_argument("--api-key", "-k", help="API key (falls back to env GOOGLE_API_KEY)")
    p.add_argument("--out", "-o", help="Write response JSON to file")
    args = p.parse_args()

    api_key = args.api_key or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("Error: API key not provided. Set GOOGLE_API_KEY or pass --api-key.")
        return 2

    status, data = generate_content(api_key=api_key, prompt=args.prompt, model=args.model)
    print(f"HTTP {status}")
    if isinstance(data, (dict, list)):
        pretty = json.dumps(data, indent=2, ensure_ascii=False)
        print(pretty)
    else:
        print(data)

    if args.out and isinstance(data, (dict, list)):
        try:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"Wrote response to {args.out}")
        except Exception as e:
            print(f"Failed to write output file: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
