"""Google Provider — Gemini models with streaming"""

import os
import json
import httpx
from .base import BaseProvider, Message, ProviderResponse


class GoogleProvider(BaseProvider):
    name = "google"

    def __init__(self):
        self.api_key = os.getenv("GOOGLE_API_KEY", "")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _build_contents(self, messages, system):
        contents = []
        if system:
            contents.append({"role": "user", "parts": [{"text": f"[System]: {system}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        for m in messages:
            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})
        return contents

    async def complete(self, messages, model_id, system=None, max_tokens=2048, stream=False) -> ProviderResponse:
        contents = self._build_contents(messages, system)
        body = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
        url = f"{self.base_url}/models/{model_id}:generateContent?key={self.api_key}"

        try:
            if stream:
                # For streaming, return a generator
                async def stream_gen():
                    async with httpx.AsyncClient(timeout=60) as client:
                        async with client.stream("POST", url.replace("generateContent", "streamGenerateContent"), json=body) as r:
                            async for line in r.aiter_lines():
                                line = line.strip()
                                if not line or line == "[" or line == "]":
                                    continue
                                clean = line.lstrip(",").strip()
                                try:
                                    chunk = json.loads(clean)
                                    text = chunk.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                                    if text:
                                        yield text
                                except Exception:
                                    pass
                return stream_gen()

            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(url, json=body)
                try:
                    r.raise_for_status()
                except httpx.HTTPStatusError as e:
                    status = getattr(getattr(e, "response", None), "status_code", "?")
                    try:
                        preview = e.response.text[:200]
                    except Exception:
                        preview = "(unable to read error body)"
                    try:
                        headers = dict(e.response.headers or {})
                    except Exception:
                        headers = {}
                    ra = headers.get("retry-after") or headers.get("Retry-After")
                    msg = f"Provider HTTP {status}: {preview}" + (f" (retry-after={ra})" if ra else "")
                    return ProviderResponse(content="", model_id=model_id, provider=self.name, success=False, error=msg)
                data = r.json()
                text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                return ProviderResponse(content=text, model_id=model_id, provider=self.name)
        except Exception as e:
            return ProviderResponse(content="", model_id=model_id, provider=self.name, success=False, error=str(e))

    async def _stream(self, headers, body, model_id):
        """Unified streaming interface matching other providers."""
        # Extract URL from context if needed
        url = f"{self.base_url}/models/{model_id}:streamGenerateContent?key={self.api_key}"
        
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream("POST", url, json=body) as r:
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line or line == "[" or line == "]":
                            continue
                        clean = line.lstrip(",").strip()
                        try:
                            chunk = json.loads(clean)
                            text = chunk.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                            if text:
                                yield text
                        except Exception:
                            pass
        except Exception as e:
            yield f"[Error: {str(e)}]"
