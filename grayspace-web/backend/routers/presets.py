"""Presets (services/presets.py): named sets of runtime changes on top of the base. Enclaves have
none (contract 1.19): their routes are gone.

    GET, POST /api/v1/base/presets                  the base version's presets; save the tracked changes as one
    GET, PUT, DELETE /api/v1/base/presets/{name}    one (?base= another version)
    POST /api/v1/base/presets/{name}/apply          Reset changes, then the preset (name "none": the base as deployed)
"""
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from models.presets import ApplyPresetResult, Preset, PresetSummary, SavePresetRequest
from services import presets
from services.auth import require_token

router = APIRouter(prefix="/api/v1", tags=["presets"], dependencies=[Depends(require_token)])

NONE = "none"


def _call(fn, *args):
    try:
        return fn(*args)
    except presets.PresetError as e:
        raise HTTPException(e.status, str(e)) from None


async def _await(coro):
    try:
        return await coro
    except presets.PresetError as e:
        raise HTTPException(e.status, str(e)) from None


@router.get("/base/presets", response_model=List[PresetSummary])
async def list_base_presets(base: Optional[str] = Query(None, description="A core version; the deployed one by default")):
    """The core version's presets, saved from any deployment on it. problems lists the targets this
    deployment doesn't have (applying skips them)."""
    return _call(presets.list_base, base)


@router.post("/base/presets", response_model=Preset)
async def save_base_preset(request: SavePresetRequest):
    """Save the tracked changes (GET /changes, the active preset's included) as a preset of the
    deployed core version, for every deployment on it. It becomes the active preset."""
    return _call(presets.save_base, request.name, request.description)


@router.get("/base/presets/{name}", response_model=Preset)
async def get_base_preset(name: str, base: Optional[str] = Query(None)):
    return _call(presets.get_base, name, base)


@router.put("/base/presets/{name}", response_model=Preset)
async def put_base_preset(name: str, preset: Preset = Body(...)):
    """Store a preset as given, e.g. one downloaded from another server, under its own core version."""
    if preset.name != name:
        raise HTTPException(422, f"The preset is named '{preset.name}', not '{name}'")
    return _call(presets.put_base, preset)


@router.delete("/base/presets/{name}", status_code=204)
async def delete_base_preset(name: str, base: Optional[str] = Query(None)):
    """Delete it for every deployment on the version. Where it is active its changes stay, as changes on the core."""
    _call(presets.delete_base, name, base)


@router.post("/base/presets/{name}/apply", response_model=ApplyPresetResult)
async def apply_base_preset(name: str):
    """Undo every tracked change, then apply the preset; "none" stops at the core as deployed."""
    return await _await(presets.apply_base(None if name == NONE else name))

