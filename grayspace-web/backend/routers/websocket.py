"""WebSocket endpoints for real-time updates."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.websocket_manager import websocket_manager
from tasks.state_monitor import state_monitor
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time network state updates.

    Clients connect to this endpoint to receive link state changes and other events.
    """
    await websocket_manager.connect(websocket)

    try:
        # Send initial link state snapshot
        link_states = []
        for link_id, state_data in state_monitor.previous_link_states.items():
            link_states.append({
                "link_id": link_id,
                "state": state_data.get("state", "unknown"),
                "diagnostics": state_data.get("diagnostics", []),
            })
        await websocket_manager.send_personal(
            {
                "type": "initial_link_states",
                "link_states": link_states
            },
            websocket
        )

        # Keep connection alive and handle incoming messages
        while True:
            data = await websocket.receive_text()
            # Echo back for heartbeat/testing
            await websocket.send_json({"type": "pong", "message": data})

    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        websocket_manager.disconnect(websocket)
