"""
Tool: Files
Read, write, edit, list, and summarize files.
Supports .txt, .md, .py, .json, .csv, .docx, .pdf
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlparse


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

        # Normalize content to a string deterministically
        if isinstance(content, (dict, list)):
            content_str = json.dumps(content, indent=2, ensure_ascii=False)
        else:
            content_str = str(content)

        # Safety: do not allow null bytes
        if "\x00" in content_str:
            return FileResult(success=False, error="Refusing to write content with null bytes")

        p.write_text(content_str, encoding="utf-8")

        if not p.exists():
            return FileResult(success=False, error=f"File was not created at {p}")

        return FileResult(success=True, path=str(p), content=f"File written: {p}")
    except Exception as e:
        return FileResult(success=False, error=str(e))



def download_url(url: str, path: str | None = None, overwrite: bool = False) -> FileResult:
    """Download a URL to a local file path."""
    if not url:
        return FileResult(success=False, error="No URL provided.")

    # Ensure url is a string
    url = str(url).strip()
    
    try:
        import httpx
    except ImportError:
        return FileResult(success=False, error="httpx not installed. Run: pip install httpx")

    try:
        parsed = urlparse(url)
        if not path:
            filename = Path(parsed.path).name or "downloaded.file"
            path = filename

        p = Path(path).expanduser().resolve()
        if p.exists() and not overwrite:
            return FileResult(success=False, error=f"File exists. Pass overwrite=True to replace: {path}")

        p.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            p.write_bytes(resp.content)

        # Verify file was written
        if not p.exists():
            return FileResult(success=False, error=f"File was not created at {p}")
        
        size = p.stat().st_size
        return FileResult(success=True, path=str(p), content=f"Downloaded {size} bytes to {p}")
    except Exception as e:
        return FileResult(success=False, error=f"Download failed: {str(e)}")


_PLACEHOLDER_MARKERS = {"todo", "tbd", "placeholder", "// your code here", "# your code here", ""}


def create_project(name: str, base_path: str | None = None, files_dict: dict[str, str] | None = None) -> FileResult:
    """Create a new project directory with real files.

    Args:
        name: Project name
        base_path: Where to create the project (default: ~/Downloads)
        files_dict: Dict of {filename: content}. Required — must contain at least
            one file with real, non-placeholder content. If the caller doesn't know
            yet what the project should contain, it should ask the user first instead
            of calling this tool.

    Example:
        create_project("calculator", files_dict={
            "main.py": "print('hello')",
            "README.md": "# Calculator"
        })
    """
    if not name:
        return FileResult(success=False, error="Project name required.")

    if not files_dict or not isinstance(files_dict, dict) or len(files_dict) == 0:
        return FileResult(
            success=False,
            error=(
                "files_dict is required and cannot be empty. Don't create an empty project — "
                "ask the user what the project should contain (purpose, language/stack, key files), "
                "then call create_project again with real file content."
            ),
        )

    empty_or_placeholder = [
        fname for fname, content in files_dict.items()
        if not str(content).strip() or str(content).strip().lower() in _PLACEHOLDER_MARKERS
    ]
    if empty_or_placeholder:
        return FileResult(
            success=False,
            error=(
                f"These files have no real content: {', '.join(empty_or_placeholder)}. "
                "Ask the user for the details needed to write actual content, then retry."
            ),
        )

    try:
        base = Path(base_path or "~/Downloads").expanduser().resolve()
        project_dir = base / name

        # Create project directory
        project_dir.mkdir(parents=True, exist_ok=True)

        created_files = []
        for filename, content in files_dict.items():
            file_path = project_dir / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(str(content), encoding="utf-8")

            # Verify file was created
            if not file_path.exists():
                return FileResult(success=False, error=f"Failed to create {filename}")
            created_files.append(filename)

        summary = f"Project '{name}' created at {project_dir}\nFiles: {', '.join(created_files)}"
        return FileResult(success=True, path=str(project_dir), content=summary)
    except Exception as e:
        return FileResult(success=False, error=f"Project creation failed: {str(e)}")


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
