"""Bound provider stalls and expose errors without leaking credentials in URLs."""

import httpx
import os
import re
from .configuration import redact_secrets


def request_timeout(read: float = 45) -> httpx.Timeout:
    return httpx.Timeout(read, connect=5.0, pool=5.0)


def provider_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        after = exc.response.headers.get("retry-after", "")
        suffix = f" (retry-after={after})" if after.replace(".", "", 1).isdigit() else ""
        detail = ""
        try:
            error = exc.response.json().get("error", {})
            detail = error.get("message", "") if isinstance(error, dict) else ""
            detail = str(detail)
            detail = redact_secrets(detail)
            detail = re.sub(r"(?i)(key|token|authorization)=\S+", r"\1=[redacted]", detail)[:400]
        except Exception:
            pass
        return f"Provider HTTP {exc.response.status_code}{suffix}" + (f": {detail}" if detail else "")
    return f"Provider error: {type(exc).__name__}"
