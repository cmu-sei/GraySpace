"""GET /api/v1/events: a websocket of map feature updates, job and instance progress, and actions.
The schema is openapi/grayspace-events-v1.json."""
import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services import auth, events

router = APIRouter(tags=["events"])

PATH = "/api/v1/events"


@router.websocket(PATH)
async def event_stream(websocket: WebSocket):
    """Authenticate with an Authorization: Bearer header, or ?token= where headers can't be set."""
    if auth.problem(auth.websocket_token(websocket.scope)):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue = events.subscribe()
    getter = receiver = None
    try:
        await websocket.send_json({"type": "hello", "schema": "grayspace-events-v1"})
        getter = asyncio.create_task(queue.get())
        receiver = asyncio.create_task(websocket.receive_text())
        while True:
            done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                await websocket.send_json(getter.result())
                getter = asyncio.create_task(queue.get())
            if receiver in done:
                if receiver.result() == "ping":
                    await websocket.send_json({"type": "pong"})
                receiver = asyncio.create_task(websocket.receive_text())
    except WebSocketDisconnect:
        pass
    finally:
        events.unsubscribe(queue)
        for task in (getter, receiver):
            if task is not None:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
