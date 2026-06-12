"""
Tool: Files
Read, write, edit, list, and summarize files.
Supports .txt, .md, .py, .json, .csv, .docx, .pdf
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass


@dataclass
class FileResult:
    success: bool
    content: str | None = None
    path: str | None = None
    error: str | None = None


def read_file(path: str, max_chars: int = 8000) -> FileResult:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return FileResult(success=False, error=f"File not found: {path}")

    suffix = p.suffix.lower()

    try:
        # Plain text types
        if suffix in {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".log", ".yaml", ".yml", ".toml", ".env"}:
            content = p.read_text(encoding="utf-8", errors="replace")
            return FileResult(success=True, content=content[:max_chars], path=str(p))

        # PDF
        elif suffix == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(str(p))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                return FileResult(success=True, content=text[:max_chars], path=str(p))
            except ImportError:
                return FileResult(success=False, error="pypdf not installed. Run: pip install pypdf")

        # DOCX
        elif suffix == ".docx":
            try:
                import docx
                doc = docx.Document(str(p))
                text = "\n".join(p.text for p in doc.paragraphs)
                return FileResult(success=True, content=text[:max_chars], path=str(p))
            except ImportError:
                return FileResult(success=False, error="python-docx not installed. Run: pip install python-docx")

        else:
            return FileResult(success=False, error=f"Unsupported file type: {suffix}")

    except Exception as e:
        return FileResult(success=False, error=str(e))


def write_file(path: str, content: str, overwrite: bool = False) -> FileResult:
    p = Path(path).expanduser().resolve()
    if p.exists() and not overwrite:
        return FileResult(success=False, error=f"File exists. Pass overwrite=True to replace: {path}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return FileResult(success=True, path=str(p))
    except Exception as e:
        return FileResult(success=False, error=str(e))


def append_file(path: str, content: str) -> FileResult:
    p = Path(path).expanduser().resolve()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return FileResult(success=True, path=str(p))
    except Exception as e:
        return FileResult(success=False, error=str(e))


def list_dir(path: str = ".") -> FileResult:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        return FileResult(success=False, error=f"Not a directory: {path}")
    try:
        entries = []
        for item in sorted(p.iterdir()):
            kind = "📁" if item.is_dir() else "📄"
            size = f"{item.stat().st_size:,} bytes" if item.is_file() else ""
            entries.append(f"{kind} {item.name}  {size}")
        return FileResult(success=True, content="\n".join(entries), path=str(p))
    except Exception as e:
        return FileResult(success=False, error=str(e))


def delete_file(path: str, confirm: bool = False) -> FileResult:
    """Safety: requires confirm=True explicitly."""
    if not confirm:
        return FileResult(success=False, error="Set confirm=True to delete. Asrār will ask you first.")
    p = Path(path).expanduser().resolve()
    try:
        p.unlink()
        return FileResult(success=True, path=str(p))
    except Exception as e:
        return FileResult(success=False, error=str(e))
