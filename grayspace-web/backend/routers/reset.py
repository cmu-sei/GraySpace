"""Reset operations API endpoints."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from services.reset_service import reset_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reset", tags=["reset"])


@router.get("/status")
async def get_reset_status():
    """
    Get the current status of reset operations.

    Returns:
        Status information including whether an operation is running.
    """
    return reset_service.get_status()


@router.post("/quick", deprecated=True)
async def run_quick_revert():
    """
    Run a quick revert of all tracked mutations. Deprecated: POST /api/v1/changes/discard does the
    same and keeps the active preset; GET /api/v1/changes lists what it undoes.

    Returns a streaming response with the operation output.
    """
    if reset_service.is_running:
        raise HTTPException(
            status_code=409,
            detail="Another reset operation is already in progress"
        )

    async def generate():
        async for line in reset_service.run_quick_revert():
            yield f"data: {line}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/cancel")
async def cancel_reset():
    """
    Cancel the current running reset operation.

    Returns:
        Success message if operation was cancelled.
    """
    if not reset_service.is_running:
        raise HTTPException(
            status_code=400,
            detail="No reset operation is currently running"
        )

    cancelled = await reset_service.cancel_operation()
    if cancelled:
        return {"status": "success", "message": "Reset operation cancelled"}
    else:
        raise HTTPException(
            status_code=500,
            detail="Failed to cancel reset operation"
        )
