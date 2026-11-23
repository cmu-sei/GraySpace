"""Runtime changes to the base as actions (services/base_changes.py).

    POST /api/v1/base/actions   one change: link.condition, link.clear, interface.zone,
                                dns.domain.create, dns.record.set

The web UI's routes for the same changes (links, interface zones, DNS) call act() too.
"""
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from models.presets import PresetAction
from services import base_changes
from services.auth import require_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/base", tags=["base-actions"], dependencies=[Depends(require_token)])


async def act(action: PresetAction) -> Any:
    """Apply one change, as an HTTP error when it isn't made."""
    try:
        return await base_changes.apply(action)
    except base_changes.ChangeError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from None
    except Exception as e:
        logger.error(f"Error applying {action.action} {action.target or ''}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from None


@router.post("/actions")
async def apply_base_action(action: PresetAction = Body(...)):
    """One runtime change to the core, tracked like the web UI's (GET /changes) and undone by Reset
    changes. Returns what the matching web UI route returns."""
    return await act(action)
