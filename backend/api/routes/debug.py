"""
Route: /debug
Diagnostic endpoints for troubleshooting file creation and tool execution
"""

from fastapi import APIRouter
from pathlib import Path
import json
import os

router = APIRouter()


@router.get("/test-file-creation")
async def test_file_creation():
    """Test if the agent can create files."""
    try:
        test_dir = Path.home() / "Downloads" / "asrar_test"
        test_dir.mkdir(parents=True, exist_ok=True)
        
        test_file = test_dir / "test_file.txt"
        test_file.write_text("Asrār test file created successfully!")
        
        if test_file.exists():
            size = test_file.stat().st_size
            return {
                "success": True,
                "message": "File creation works!",
                "path": str(test_file),
                "size_bytes": size,
                "readable": test_file.read_text()[:100]
            }
        else:
            return {"success": False, "error": "File was written but not found after check"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/test-project-creation")
async def test_project_creation():
    """Test if the agent can create projects."""
    try:
        from tools.files import create_project
        
        result = create_project(
            name="asrar_test_project",
            base_path=str(Path.home() / "Downloads"),
            files_dict={
                "README.md": "# Test Project\nCreated by Asr\u0101r",
                "main.py": "print('Hello from Asr\u0101r')",
                "config.json": json.dumps({"name": "test", "version": "1.0"})
            }
        )
        
        if result.success:
            project_path = Path(result.path)
            files = list(project_path.glob("*"))
            return {
                "success": True,
                "message": "Project creation works!",
                "path": str(project_path),
                "files_created": [f.name for f in files],
                "summary": result.content
            }
        else:
            return {"success": False, "error": result.error}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/check-file/{path:path}")
async def check_file(path: str):
    """Check if a file exists and get its properties."""
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return {"exists": False, "path": str(p)}
        
        if p.is_file():
            return {
                "exists": True,
                "type": "file",
                "path": str(p),
                "size_bytes": p.stat().st_size,
                "readable": p.read_text(errors="ignore")[:500] if p.suffix in [".txt", ".md", ".json", ".py"] else None
            }
        else:
            items = list(p.iterdir())
            return {
                "exists": True,
                "type": "directory",
                "path": str(p),
                "contents": [{"name": i.name, "type": "dir" if i.is_dir() else "file"} for i in sorted(items)]
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/env-keys")
async def check_env_keys():
    """Check which API keys are configured."""
    from providers.configuration import PROVIDER_DEFAULTS, setting
    from core.registry import list_models
    names = {provider["env_key"] for provider in PROVIDER_DEFAULTS.values()}
    names.update(model["api_key_env"] for model in list_models() if model.get("api_key_env"))
    return {name: {"configured": bool(setting(name).strip())} for name in sorted(names)}
