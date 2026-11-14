"""Bound provider stalls and expose errors without leaking credentials in URLs."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import re
import weakref

import httpx
from .configuration import redact_secrets


def request_timeout(read: float = 45) -> httpx.Timeout:
    return httpx.Timeout(read, connect=5.0, pool=5.0)


# Pools exist only inside an explicit application lifespan. Short-lived scripts
# keep ordinary context-managed clients and cannot strand sockets on closed loops.
_client_pools = weakref.WeakKeyDictionary()


@asynccontextmanager
async def provider_client_pool():
    """Own keepalive clients on this event loop and close every client on exit."""
    loop = asyncio.get_running_loop()
    if loop in _client_pools:
        yield  # A nested scope borrows the outer owner's clients.
        return
    clients = {}
    _client_pools[loop] = clients
    try:
        yield
    finally:
        _client_pools.pop(loop, None)
        if clients:
            await asyncio.gather(*(client.aclose() for client in clients.values()), return_exceptions=True)
            clients.clear()


@asynccontextmanager
async def provider_client(provider_name: str, base_url: str, headers: dict, read_timeout: float = 45):
    """Borrow a credential-scoped client; request headers stay request-local."""
    clients = _client_pools.get(asyncio.get_running_loop())
    if clients is None:
        async with httpx.AsyncClient(timeout=request_timeout(read_timeout)) as client:
            yield client
        return
    # Never retain auth headers on the shared client. A fingerprint additionally
    # keeps provider cookies and connections apart after an account/key change.
    fingerprint = hashlib.sha256(json.dumps(sorted((str(k).lower(), str(v)) for k, v in headers.items())).encode()).digest()
    key = (provider_name, base_url.rstrip('/'), fingerprint, read_timeout)
    client = clients.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=request_timeout(read_timeout))
        clients[key] = client
    yield client


def token_count(value) -> int:
    """Usage is optional and provider payloads are not uniformly well-typed."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


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
