"""Presets (services/presets.py): named sets of runtime changes on top of the base. Enclaves have
none (contract 1.19): their routes are gone.

    GET, POST /api/v1/core/presets                  the core version's presets; save the tracked changes as one
    GET, PUT, DELETE /api/v1/core/presets/{name}    one (?core= another version)
    POST /api/v1/core/presets/{name}/apply          Reset changes, then the preset (name "none": the core as deployed)

Also at /api/v1/base/presets, the names before contract 1.26 (deprecated; ?base= for ?core=).
"""
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from models.presets import ApplyPresetResult, Preset, PresetSummary, SavePresetRequest
from services import presets
from services.auth import require_token

routes = APIRouter()

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


@routes.get("/presets", response_model=List[PresetSummary])
async def list_base_presets(core: Optional[str] = Query(None, description="A core version; the deployed one by default"),
                            base: Optional[str] = Query(None, description="Deprecated (contract 1.26): the same as core", deprecated=True)):
    """The core version's presets, saved from any deployment on it. problems lists the targets this
    deployment doesn't have (applying skips them)."""
    return _call(presets.list_base, core or base)


@routes.post("/presets", response_model=Preset)
async def save_base_preset(request: SavePresetRequest):
    """Save the tracked changes (GET /changes, the active preset's included) as a preset of the
    deployed core version, for every deployment on it. It becomes the active preset."""
    return _call(presets.save_base, request.name, request.description)


@routes.get("/presets/{name}", response_model=Preset)
async def get_base_preset(name: str, core: Optional[str] = Query(None, description="A core version; the deployed one by default"),
                          base: Optional[str] = Query(None, description="Deprecated (contract 1.26): the same as core", deprecated=True)):
    return _call(presets.get_base, name, core or base)


@routes.put("/presets/{name}", response_model=Preset)
async def put_base_preset(name: str, preset: Preset = Body(...)):
    """Store a preset as given, e.g. one downloaded from another server, under its own core version."""
    if preset.name != name:
        raise HTTPException(422, f"The preset is named '{preset.name}', not '{name}'")
    return _call(presets.put_base, preset)


@routes.delete("/presets/{name}", status_code=204)
async def delete_base_preset(name: str, core: Optional[str] = Query(None, description="A core version; the deployed one by default"),
                             base: Optional[str] = Query(None, description="Deprecated (contract 1.26): the same as core", deprecated=True)):
    """Delete it for every deployment on the version. Where it is active its changes stay, as changes on the core."""
    _call(presets.delete_base, name, core or base)


@routes.post("/presets/{name}/apply", response_model=ApplyPresetResult)
async def apply_base_preset(name: str):
    """Undo every tracked change, then apply the preset; "none" stops at the core as deployed."""
    return await _await(presets.apply_base(None if name == NONE else name))


router = APIRouter(tags=["presets"], dependencies=[Depends(require_token)])
router.include_router(routes, prefix="/api/v1/core")
# The paths before contract 1.26, when the core was the base
legacy_router = APIRouter(tags=["presets"], deprecated=True, dependencies=[Depends(require_token)])
legacy_router.include_router(routes, prefix="/api/v1/base")
