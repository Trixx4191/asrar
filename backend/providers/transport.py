"""Bound provider stalls and expose errors without leaking credentials in URLs."""

import httpx


def request_timeout(read: float = 45) -> httpx.Timeout:
    return httpx.Timeout(read, connect=5.0, pool=5.0)


def provider_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        after = exc.response.headers.get("retry-after", "")
        suffix = f" (retry-after={after})" if after.replace(".", "", 1).isdigit() else ""
        return f"Provider HTTP {exc.response.status_code}{suffix}"
    return f"Provider error: {type(exc).__name__}"
