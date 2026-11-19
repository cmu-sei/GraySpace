"""Desktop (noVNC) WebSocket proxy endpoint."""
import asyncio
import logging
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.proxmox_client import proxmox_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["desktop"])


@router.websocket("/ws/desktop/{hostname}")
async def desktop_websocket(websocket: WebSocket, hostname: str):
    """
    WebSocket proxy for noVNC desktop access.

    Bridges the browser's noVNC WebSocket to the websockify
    instance running inside the LXC container (port 6080).
    """
    await websocket.accept()

    container = await asyncio.to_thread(proxmox_client.get_container_by_hostname, hostname)
    if not container:
        await websocket.close(code=4004, reason=f"Container {hostname} not found")
        return

    if container["status"] != "running":
        await websocket.close(code=4003, reason=f"Container {hostname} is not running")
        return

    mgmt_ip = await asyncio.to_thread(proxmox_client.get_container_management_ip, hostname)
    if not mgmt_ip:
        await websocket.close(code=4002, reason=f"Cannot resolve IP for {hostname}")
        return

    vnc_ws_url = f"ws://{mgmt_ip}:6080"
    try:
        vnc_ws = await websockets.connect(
            vnc_ws_url,
            subprotocols=["binary"],
            max_size=2**22,
            open_timeout=10,
        )
    except Exception as e:
        logger.error(f"Failed to connect to websockify at {vnc_ws_url}: {e}")
        await websocket.close(code=4001, reason="Desktop service unavailable")
        return

    logger.info(f"Desktop proxy connected for {hostname} via {mgmt_ip}:6080")

    async def browser_to_vnc():
        try:
            while True:
                data = await websocket.receive_bytes()
                await vnc_ws.send(data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"browser_to_vnc ended: {e}")

    async def vnc_to_browser():
        try:
            async for message in vnc_ws:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)
        except Exception as e:
            logger.debug(f"vnc_to_browser ended: {e}")

    try:
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(browser_to_vnc()),
                asyncio.create_task(vnc_to_browser()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        await vnc_ws.close()
        logger.info(f"Desktop proxy session ended for {hostname}")
