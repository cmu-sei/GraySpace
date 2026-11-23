"""Runtime changes to the base as actions (services/base_changes.py).

    POST /api/v1/core/actions   one change: link.condition, link.clear, interface.zone,
                                dns.domain.create, dns.record.set

POST /api/v1/base/actions is the same, the path before contract 1.26 (deprecated).
The web UI's routes for the same changes (links, interface zones, DNS) call act() too.
"""
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from models.presets import PresetAction
from services import base_changes
from services.auth import require_token

logger = logging.getLogger(__name__)

routes = APIRouter()


async def act(action: PresetAction) -> Any:
    """Apply one change, as an HTTP error when it isn't made."""
    try:
        return await base_changes.apply(action)
    except base_changes.ChangeError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from None
    except Exception as e:
        logger.error(f"Error applying {action.action} {action.target or ''}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from None


@routes.post("/actions")
async def apply_base_action(action: PresetAction = Body(...)):
    """One runtime change to the core, tracked like the web UI's (GET /changes) and undone by Reset
    changes. Returns what the matching web UI route returns."""
    return await act(action)


router = APIRouter(tags=["core-actions"], dependencies=[Depends(require_token)])
router.include_router(routes, prefix="/api/v1/core")
# The path before contract 1.26, when the core was the base
legacy_router = APIRouter(tags=["core-actions"], deprecated=True, dependencies=[Depends(require_token)])
legacy_router.include_router(routes, prefix="/api/v1/base")
