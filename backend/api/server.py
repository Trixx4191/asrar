"""
Asrār — FastAPI Server
Bridges the frontend (Electron) to the agent backend.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "core"))

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from api.routes import chat, models, tasks, debug, conversations, skills, connections
from core import memory
from core.kimi_bridge import bridge as kimi_bridge
from core.codex_bridge import bridge
from core.claude_bridge import bridge as claude_bridge

memory.init_db()

@asynccontextmanager
async def lifespan(app):
    yield
    await bridge.close()
    await claude_bridge.close()
    await kimi_bridge.close()


app = FastAPI(title="Asrār Agent API", version="0.14.0", lifespan=lifespan)

TRUSTED_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"}


@app.middleware("http")
async def local_client_only(request: Request, call_next):
    host = request.url.hostname
    if host not in {"localhost", "127.0.0.1", "::1", "testserver"}:
        return JSONResponse({"detail": "Asrār accepts local hostnames only"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin not in TRUSTED_ORIGINS and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "Untrusted desktop origin"}, status_code=403)
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(TRUSTED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router,          prefix="/chat",          tags=["chat"])
app.include_router(models.router,        prefix="/models",        tags=["models"])
app.include_router(tasks.router,         prefix="/tasks",         tags=["tasks"])
app.include_router(debug.router,         prefix="/debug",         tags=["debug"])
app.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
app.include_router(skills.router, prefix="/skills", tags=["skills"])
app.include_router(connections.router, prefix="/connections", tags=["connections"])


@app.get("/")
async def root():
    return {"status": "ok", "agent": "Asrār", "version": "0.14.0"}


@app.get("/health")
async def health():
    return {"status": "alive"}


if (ROOT / "frontend" / "dist").is_dir():
    app.mount("/desktop", StaticFiles(directory=ROOT / "frontend" / "dist", html=True), name="desktop")
