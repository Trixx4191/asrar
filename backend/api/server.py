"""
Asrār — FastAPI Server
Bridges the frontend (Electron) to the agent backend.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "core"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from api.routes import chat, models, tasks

app = FastAPI(title="Asrār Agent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router,   prefix="/chat",   tags=["chat"])
app.include_router(models.router, prefix="/models", tags=["models"])
app.include_router(tasks.router,  prefix="/tasks",  tags=["tasks"])


@app.get("/")
async def root():
    return {"status": "ok", "agent": "Asrār", "version": "0.1.0"}


@app.get("/health")
async def health():
    return {"status": "alive"}
