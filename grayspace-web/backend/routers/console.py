"""Console access WebSocket API endpoints."""
import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from services.console_service import console_manager, ConsoleSession
from services.proxmox_client import proxmox_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["console"])


@router.websocket("/ws/console/{hostname}")
async def console_websocket(
    websocket: WebSocket,
    hostname: str,
    cols: int = Query(default=80, ge=10, le=500),
    rows: int = Query(default=24, ge=5, le=200)
):
    """
    WebSocket endpoint for interactive console access to a router.

    Protocol:
    - Client connects with hostname and optional cols/rows params
    - Server sends {"type": "connected", "session_id": "..."} on success
    - Server sends {"type": "error", "message": "..."} on failure
    - Client sends {"type": "input", "data": "..."} for terminal input
    - Client sends {"type": "resize", "cols": N, "rows": M} for terminal resize
    - Client sends {"type": "ping"} for keepalive
    - Server sends {"type": "output", "data": "..."} for terminal output
    - Server sends {"type": "pong"} in response to ping
    - Server sends {"type": "disconnected"} when SSH session ends
    """
    await websocket.accept()

    # Verify container belongs to this deployment and is running
    container = await asyncio.to_thread(proxmox_client.get_container_by_hostname, hostname)
    if not container:
        await websocket.send_json({
            "type": "error",
            "message": f"Container {hostname} not found or not in this deployment"
        })
        await websocket.close()
        return

    if container["status"] != "running":
        await websocket.send_json({
            "type": "error",
            "message": f"Container {hostname} is not running"
        })
        await websocket.close()
        return

    # Create console session
    session = await console_manager.create_session(hostname, cols, rows)
    if not session:
        await websocket.send_json({
            "type": "error",
            "message": f"Failed to establish SSH connection to {hostname}"
        })
        await websocket.close()
        return

    # Send connected message
    await websocket.send_json({
        "type": "connected",
        "session_id": session.session_id,
        "hostname": hostname
    })

    # Start read and write tasks
    read_task = asyncio.create_task(_read_ssh_output(websocket, session))
    write_task = asyncio.create_task(_handle_websocket_input(websocket, session))

    try:
        # Wait for either task to complete (means connection ended)
        done, pending = await asyncio.wait(
            [read_task, write_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        # Cancel pending tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"Error in console websocket for {hostname}: {e}")
    finally:
        # Clean up
        await console_manager.close_session(session.session_id)

        try:
            await websocket.send_json({"type": "disconnected"})
        except Exception:
            pass

        logger.info(f"Console session ended for {hostname}")


async def _read_ssh_output(websocket: WebSocket, session: ConsoleSession):
    """Read output from SSH and send to WebSocket."""
    try:
        while session.is_connected:
            data = await session.ssh_session.read(4096)
            if data:
                session.touch()
                await websocket.send_json({
                    "type": "output",
                    "data": data.decode('utf-8', errors='replace')
                })
            else:
                # No data available, brief pause to avoid busy loop
                await asyncio.sleep(0.01)
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session.session_id}")
    except Exception as e:
        logger.error(f"Error reading SSH output: {e}")


async def _handle_websocket_input(websocket: WebSocket, session: ConsoleSession):
    """Handle input from WebSocket and send to SSH."""
    try:
        while session.is_connected:
            message = await websocket.receive_json()
            session.touch()

            msg_type = message.get("type")

            if msg_type == "input":
                # Terminal input
                data = message.get("data", "")
                if data:
                    await session.ssh_session.write(data.encode('utf-8'))

            elif msg_type == "resize":
                # Terminal resize
                cols = message.get("cols", 80)
                rows = message.get("rows", 24)
                await session.ssh_session.resize(cols, rows)

            elif msg_type == "ping":
                # Keepalive
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session.session_id}")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON from WebSocket: {e}")
    except Exception as e:
        logger.error(f"Error handling WebSocket input: {e}")


@router.get("/api/v1/console/sessions")
async def get_active_sessions():
    """
    Get information about active console sessions.

    Returns:
        List of active session info.
    """
    return {
        "active_sessions": console_manager.get_session_count(),
        "sessions": [
            {
                "session_id": s.session_id,
                "hostname": s.hostname,
                "created_at": s.created_at.isoformat(),
                "last_activity": s.last_activity.isoformat(),
                "connected": s.is_connected
            }
            for s in console_manager._sessions.values()
        ]
    }


@router.delete("/api/v1/console/session/{session_id}")
async def close_session(session_id: str):
    """
    Close a console session.

    Args:
        session_id: Session ID to close.

    Returns:
        Success message.
    """
    closed = await console_manager.close_session(session_id)
    if closed:
        return {"status": "success", "message": f"Session {session_id} closed"}
    return {"status": "not_found", "message": f"Session {session_id} not found"}
