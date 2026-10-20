"""Check registered model IDs against a provider's authenticated catalog."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "backend"))

from core.router import load_registry
from providers import get_provider
from providers.transport import provider_error


async def check(name):
    provider = get_provider(name)
    if not provider.is_available():
        print(json.dumps({"provider": name, "error": "API key is not configured"}))
        return False
    headers = provider.headers()
    if name == "google":
        headers["x-goog-api-key"] = provider.api_key
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5)) as client:
            response = await client.get(f"{provider.base_url}/models", headers=headers)
            response.raise_for_status()
        payload = response.json()
        ids = {row.get("id") or row.get("name", "").removeprefix("models/")
               for row in payload.get("data", payload.get("models", []))}
        registered = {key: {"id": m["id"], "available": m["id"] in ids}
                      for key, m in load_registry()["models"].items() if m["provider"] == name}
        print(json.dumps({"provider": name, "registered": registered, "available_models": sorted(ids)}, indent=2))
        return True
    except Exception as exc:
        print(json.dumps({"provider": name, "error": provider_error(exc)}))
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", nargs="?", default="groq")
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(check(args.provider)) else 1)
