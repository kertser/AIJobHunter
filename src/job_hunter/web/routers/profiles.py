"""Profiles router — view/edit user profile and search profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_hunter.config.loader import load_profiles, load_user_profile, save_profiles, save_user_profile
from job_hunter.config.models import SearchProfile, UserProfile
from job_hunter.web.deps import get_db, get_settings, get_user_data_dir

router = APIRouter(tags=["profiles"])


@router.get("/profiles")
async def profiles_page(request: Request):
    templates = request.app.state.templates
    data_dir = get_user_data_dir(request)
    profiles_path = data_dir / "profiles.yml"
    user_profile_path = data_dir / "user_profile.yml"

    search_profiles = []
    user_profile = None
    if profiles_path.exists():
        search_profiles = load_profiles(profiles_path)
    if user_profile_path.exists():
        user_profile = load_user_profile(user_profile_path)

    return templates.TemplateResponse(request, "profiles.html", {
        "user_profile": user_profile,
        "search_profiles": search_profiles,
    })


@router.get("/api/profiles")
async def get_profiles(request: Request):
    data_dir = get_user_data_dir(request)
    profiles_path = data_dir / "profiles.yml"
    if not profiles_path.exists():
        return {"profiles": []}
    profiles = load_profiles(profiles_path)
    return {"profiles": [p.model_dump() for p in profiles]}


@router.put("/api/profiles")
async def update_profiles(request: Request):
    data_dir = get_user_data_dir(request)
    body = await request.json()
    profiles_data = body.get("profiles", body) if isinstance(body, dict) else body
    if isinstance(profiles_data, dict):
        profiles_data = profiles_data.get("profiles", [])
    profiles = [SearchProfile(**p) for p in profiles_data]
    save_profiles(profiles, data_dir / "profiles.yml")
    return {"saved": len(profiles)}


@router.get("/api/user-profile")
async def get_user_profile(request: Request):
    data_dir = get_user_data_dir(request)
    path = data_dir / "user_profile.yml"
    if not path.exists():
        return {"user_profile": None}
    up = load_user_profile(path)
    return {"user_profile": up.model_dump()}


@router.put("/api/user-profile")
async def update_user_profile(request: Request):
    data_dir = get_user_data_dir(request)
    body = await request.json()
    data = body.get("user_profile", body) if isinstance(body, dict) else body
    up = UserProfile(**data)
    save_user_profile(up, data_dir / "user_profile.yml")
    return {"saved": True}

