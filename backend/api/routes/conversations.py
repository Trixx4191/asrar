"""
Route: /conversations
CRUD for persisted chat conversations (SQLite-backed, see core/memory.py).

GET    /conversations          — list all saved chats (id, title, timestamps, message_count)
POST   /conversations          — create a new empty conversation, optional {title}
GET    /conversations/{id}     — full message history for one conversation
PATCH  /conversations/{id}     — rename a conversation, body {title}
DELETE /conversations/{id}     — delete a conversation and its messages
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import memory

router = APIRouter()


class CreateConversationRequest(BaseModel):
    title: str | None = None


class RenameConversationRequest(BaseModel):
    title: str


@router.get("")
async def list_conversations():
    return {"conversations": memory.list_conversations()}


@router.post("")
async def create_conversation(req: CreateConversationRequest):
    conv_id = memory.create_conversation(title=req.title)
    conv = memory.get_conversation(conv_id)
    return {"conversation": conv}


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str):
    conv = memory.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv["messages"] = memory.get_messages_full(conversation_id)
    conv["plan"] = memory.get_plan(conversation_id)
    return {"conversation": conv}


@router.get("/{conversation_id}/plan")
async def get_plan(conversation_id: str):
    conv = memory.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"plan": memory.get_plan(conversation_id)}


@router.patch("/{conversation_id}")
async def rename_conversation(conversation_id: str, req: RenameConversationRequest):
    ok = memory.rename_conversation(conversation_id, req.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True}


@router.get("/{conversation_id}/log")
async def get_execution_log(conversation_id: str):
    conv = memory.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"log": memory.get_log(conversation_id)}


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str):
    ok = memory.delete_conversation(conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True}
