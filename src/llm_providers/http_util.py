"""Stdlib HTTP with retry-and-backoff. Free tiers throttle; don't die silently."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from src.llm_providers.base import RateLimitError


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    provider: str,
    timeout: int = 60,
    retries: int = 3,
    inter_call_delay: float = 0.5,
) -> dict[str, Any]:
    """POST with exponential backoff on 429/5xx.

    inter_call_delay: sleep before each attempt to stay within free-tier TPM.
    On 429, parse Retry-After if present and wait that many seconds instead.
    """
    body = json.dumps(payload).encode("utf-8")
    hdrs = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) finance-controller-agent/1.0",
        **headers,
    }
    delay = 4.0  # start higher — Groq 429 says "wait 3.8s"
    last_error: Exception | None = None
    for attempt in range(retries):
        if inter_call_delay > 0 and attempt == 0:
            time.sleep(inter_call_delay)
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            last_error = exc
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:600]
            except Exception:
                detail = str(exc)
            if exc.code == 429 or exc.code >= 500:
                if attempt == retries - 1:
                    raise RateLimitError(
                        f"{provider} returned HTTP {exc.code} after {retries} retries. "
                        "Free tiers throttle aggressively. Wait a minute, or set "
                        "LLM_PROVIDER=groq|ollama|gemini to switch. "
                        f"Body: {detail}"
                    ) from exc
                # Try to honour Retry-After header
                retry_after = None
                try:
                    retry_after = float(exc.headers.get("Retry-After") or 0)
                except Exception:
                    pass
                wait = retry_after if retry_after and retry_after > 0 else delay
                time.sleep(wait)
                delay = max(delay * 2, wait + 2)
                continue
            raise RuntimeError(f"{provider} HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt == retries - 1:
                raise RuntimeError(f"{provider} connection failed: {exc}") from exc
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"{provider} request failed: {last_error}")
