"""Discover and inspect the same skill catalog supplied to the agent."""

from fastapi import APIRouter, HTTPException
from core import skills

router = APIRouter()


@router.get("")
async def list_skills():
    return {"skills": skills.list_skills()}


@router.get("/{name}")
async def get_skill(name: str):
    skill = skills.load_skill(name)
    if skill is None:
        raise HTTPException(404, "Skill not found")
    return {"skill": skill}
