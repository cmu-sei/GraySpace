"""The base's runtime changes (services/mutation_tracker.py): what they are, and Reset changes.

    GET /api/v1/changes            the active base preset and the changes since it, with who made them
    POST /api/v1/changes/discard   Reset changes: back to the active preset (services/presets.py)

/api/v1/scenarios/changes, /scenarios/mutations/count and /scenarios/discard-changes are the
same under their names before presets (deprecated).
"""
from fastapi import APIRouter, Depends

from models.presets import ApplyPresetResult, TrackedChange, TrackedChanges
from services import presets
from services.auth import require_token
from services.mutation_tracker import mutation_tracker

router = APIRouter(prefix="/api/v1/changes", tags=["changes"], dependencies=[Depends(require_token)])
legacy_router = APIRouter(prefix="/api/v1/scenarios", tags=["scenarios (deprecated)"], deprecated=True, dependencies=[Depends(require_token)])


@router.get("", response_model=TrackedChanges)
async def get_changes():
    """What Reset changes would undo: each change since the active preset, who made it and when.
    Certificates revoked are listed but stay revoked."""
    return TrackedChanges(preset=mutation_tracker.preset, base=presets.deployed_base(), baseline=mutation_tracker.preset or "GraySpace",
                          count=mutation_tracker.count(), changes=[TrackedChange(**c) for c in reversed(mutation_tracker.changes)])


@router.post("/discard", response_model=ApplyPresetResult)
async def discard_changes():
    """Undo every tracked change and apply the active preset again; with none, back to the core as deployed."""
    return await presets.discard()


legacy_router.add_api_route("/changes", get_changes, methods=["GET"], response_model=TrackedChanges)
legacy_router.add_api_route("/discard-changes", discard_changes, methods=["POST"], response_model=ApplyPresetResult)


@legacy_router.get("/mutations/count")
async def get_mutation_count():
    return {"count": mutation_tracker.count(), "baseline": mutation_tracker.preset or "GraySpace"}
