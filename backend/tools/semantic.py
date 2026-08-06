"""
Semantic / hybrid code search.

Primary: token-aware regex with TF-IDF style ranking over matching lines.
Optional: if an embedding backend is configured later, plug into embed_search().
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from tools import codesearch


@dataclass
class SemanticMatch:
    path: str
    line: int
    text: str
    score: float = 1.0


@dataclass
class SemanticResult:
    success: bool
    matches: list[SemanticMatch] = field(default_factory=list)
    method: str = "hybrid"
    error: str | None = None
    files_searched: int = 0


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+|[A-Za-z]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _camel_split(token: str) -> list[str]:
    # routeRequest -> route, request
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", token)
    return [p.lower() for p in parts if p]


def _expand_query_tokens(query: str) -> list[str]:
    raw = _tokenize(query)
    expanded = set(raw)
    for t in raw:
        expanded.update(_camel_split(t))
        if "_" in t:
            expanded.update(t.split("_"))
    return [t for t in expanded if len(t) > 1]


def _score_line(line: str, query_tokens: list[str], idf: dict[str, float]) -> float:
    line_tokens = _tokenize(line)
    if not line_tokens:
        return 0.0
    tf = Counter(line_tokens)
    score = 0.0
    for qt in query_tokens:
        if qt in tf:
            score += (1.0 + math.log(tf[qt])) * idf.get(qt, 1.0)
        else:
            # partial / substring boost
            for lt in tf:
                if qt in lt or lt in qt:
                    score += 0.35 * idf.get(qt, 1.0)
                    break
    # density: prefer shorter focused lines
    if score > 0:
        score /= math.sqrt(len(line_tokens))
    return score


def semantic_search(
    query: str,
    path: str = ".",
    glob: str | None = None,
    max_matches: int = 40,
) -> SemanticResult:
    if not query or not query.strip():
        return SemanticResult(success=False, error="Empty query")

    query_tokens = _expand_query_tokens(query)
    if not query_tokens:
        return SemanticResult(success=False, error="No usable tokens in query")

    # Broad regex: any query token (OR) to gather candidates
    pattern = "|".join(re.escape(t) for t in query_tokens[:12])
    r = codesearch.search_code(
        pattern=pattern,
        path=path,
        glob=glob,
        case_sensitive=False,
        max_matches=max(200, max_matches * 5),
    )
    if not r.success:
        return SemanticResult(success=False, error=r.error, method="hybrid")

    # Document frequency over matched lines for crude IDF
    df: Counter = Counter()
    for m in r.matches:
        toks = set(_tokenize(m.text))
        for t in query_tokens:
            if t in toks or any(t in x or x in t for x in toks):
                df[t] += 1
    n = max(len(r.matches), 1)
    idf = {t: math.log(1 + n / (1 + df.get(t, 0))) for t in query_tokens}

    scored: list[SemanticMatch] = []
    for m in r.matches:
        s = _score_line(m.text, query_tokens, idf)
        if s <= 0:
            continue
        scored.append(SemanticMatch(path=m.path, line=m.line, text=m.text, score=round(s, 4)))

    scored.sort(key=lambda x: x.score, reverse=True)
    scored = scored[:max_matches]

    return SemanticResult(
        success=True,
        matches=scored,
        method="hybrid-tfidf",
        files_searched=getattr(r, "files_searched", 0),
    )


def format_semantic_result(r: SemanticResult) -> str:
    if not r.success:
        return f"❌ Semantic search error: {r.error}"
    if not r.matches:
        return f"No matches for query (method={r.method})."
    lines = [f"Found {len(r.matches)} match(es) via {r.method} (ranked):"]
    current = None
    for m in r.matches:
        if m.path != current:
            current = m.path
            lines.append(f"\n{m.path}:")
        lines.append(f"  {m.line} [{m.score:.2f}]: {m.text[:120]}")
    return "\n".join(lines)
