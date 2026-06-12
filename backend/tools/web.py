"""
Tool: Web
Search the web and fetch page content.
Uses DuckDuckGo (no key needed) with Gemini as fallback for grounding.
"""

import httpx
from dataclasses import dataclass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class WebToolResult:
    success: bool
    results: list[SearchResult] | None = None
    page_content: str | None = None
    error: str | None = None


async def search(query: str, max_results: int = 5) -> WebToolResult:
    """Search the web using DuckDuckGo Instant Answer API."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                headers={"User-Agent": "Asrar-Agent/0.1"},
            )
            data = r.json()

        results = []

        # Abstract (top answer)
        if data.get("Abstract"):
            results.append(SearchResult(
                title=data.get("Heading", "Top Result"),
                url=data.get("AbstractURL", ""),
                snippet=data["Abstract"],
            ))

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(SearchResult(
                    title=topic.get("Text", "")[:60],
                    url=topic.get("FirstURL", ""),
                    snippet=topic.get("Text", ""),
                ))

        if not results:
            return WebToolResult(success=False, error="No results found. Try rephrasing.")

        return WebToolResult(success=True, results=results[:max_results])

    except Exception as e:
        return WebToolResult(success=False, error=str(e))


async def fetch_page(url: str, max_chars: int = 4000) -> WebToolResult:
    """Fetch and extract readable text from a webpage."""
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Asrar-Agent/0.1"})
            r.raise_for_status()
            html = r.text

        # Basic extraction — strip tags
        import re
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return WebToolResult(success=True, page_content=text[:max_chars])

    except Exception as e:
        return WebToolResult(success=False, error=str(e))


async def search_and_summarize(query: str) -> str:
    """High-level: search + return formatted results string for agent context."""
    result = await search(query)
    if not result.success:
        return f"Web search failed: {result.error}"

    lines = [f"Web results for: '{query}'\n"]
    for i, r in enumerate(result.results, 1):
        lines.append(f"{i}. {r.title}\n   {r.snippet}\n   {r.url}")
    return "\n".join(lines)
