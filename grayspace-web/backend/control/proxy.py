"""Forwarding to a deployment's worker: HTTP (streamed, for the followed job log) and websockets.

/deployments/{id}/{path} reaches {path} on the deployment's worker unchanged: query string, body
and headers. The un-prefixed /api and /ws routes reach the default deployment and are marked deprecated.

Every request is the caller's first (authorize): a session or the service token, a deployment
they can see (404 otherwise), and the permission its route needs (control/access.py, 403
otherwise). The worker then gets the server's service token in place of the caller's, and the
caller's name in X-Grayspace-User; only the control plane reaches the worker's socket.
"""
import asyncio
import contextlib
import logging
from typing import Dict, List, Optional, Tuple

import httpx
import websockets
from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect

from urllib.parse import parse_qsl, urlencode

from control import access, auth, registry
from control.access import Principal
from control.settings import settings
from control.supervisor import client, supervisor

logger = logging.getLogger(__name__)

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding",
              "upgrade", "host"}
# Replaced: the worker gets the service token and the caller's name
REPLACED = {"authorization", "x-grayspace-user"}
WS_HEADERS = {"cookie", "origin", "user-agent", "x-forwarded-for", "x-real-ip"}
USER_HEADER = "X-Grayspace-User"
# How long a request waits for a worker that is (re)starting
START_WAIT = 30.0

_clients: Dict[str, httpx.AsyncClient] = {}


def _client(deployment_id: str) -> httpx.AsyncClient:
    c = _clients.get(deployment_id)
    if c is None or c.is_closed:
        c = _clients[deployment_id] = client(deployment_id)
    return c


async def close_clients():
    for c in _clients.values():
        await c.aclose()
    _clients.clear()


def default_or_404() -> str:
    deployment_id = registry.default_id()
    if deployment_id is None:
        raise HTTPException(404, "No default deployment: use /deployments/{id}/... (GET /api/v1/deployments lists them), "
                                 "or make one the default (PATCH /api/v1/deployments/{id} {\"default\": true})")
    return deployment_id


async def available(deployment_id: str) -> None:
    if registry.get(deployment_id) is None:
        raise HTTPException(404, f"No deployment '{deployment_id}'")
    if not await supervisor.wait_ready(deployment_id, START_WAIT):
        status = supervisor.status(deployment_id)
        raise HTTPException(503, f"Deployment '{deployment_id}' isn't running ({status.state}"
                                 f"{': ' + status.error if status.error else ''})", headers={"Retry-After": "5"})


def authorize(token: Optional[str], deployment_id: str, method: str, path: str) -> Principal:
    """The caller, when they may make this request to the deployment; HTTPException otherwise."""
    principal, error = auth.problem(token)
    if error:
        raise HTTPException(error[0], error[1], headers={"WWW-Authenticate": "Bearer"} if error[0] == 401 else None)
    record = registry.get(deployment_id)
    if record is None or access.access(principal, record) is None:
        raise HTTPException(404, f"No deployment '{deployment_id}'")
    needed = access.route_class(method, path)
    if needed not in access.permissions(principal, record):
        raise HTTPException(403, f"You need {needed} on deployment '{deployment_id}' to {method} /{path.lstrip('/')}")
    return principal


def _upstream_headers(principal: Principal) -> List[Tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {settings.api_token}".encode()), (USER_HEADER.lower().encode(), principal.username.encode())]


async def forward(request: Request, deployment_id: str, path: str, principal: Principal, alias: bool = False):
    await available(deployment_id)
    headers = [(k, v) for k, v in request.headers.raw if k.decode("latin-1").lower() not in HOP_BY_HOP | REPLACED]
    headers += _upstream_headers(principal)
    url = httpx.URL(path="/" + path, query=request.url.query.encode())
    upstream = _client(deployment_id).build_request(request.method, url, headers=headers, content=request.stream())
    try:
        response = await _client(deployment_id).send(upstream, stream=True)
    except (httpx.ConnectError, httpx.RemoteProtocolError, OSError) as e:
        raise HTTPException(503, f"Deployment '{deployment_id}' didn't answer: {e}", headers={"Retry-After": "5"}) from None
    out = [(k, v) for k, v in response.headers.raw if k.decode("latin-1").lower() not in HOP_BY_HOP]
    streamed = StreamingResponse(response.aiter_raw(), status_code=response.status_code, background=BackgroundTask(response.aclose))
    streamed.raw_headers = [(k.lower(), v) for k, v in out]
    if alias:
        streamed.raw_headers += [(b"deprecation", b"true"), (b"x-grayspace-deployment", deployment_id.encode())]
    return streamed


async def relay(websocket: WebSocket, deployment_id: str, path: str):
    """Authorize first (closing before accepting refuses the handshake), then connect to the
    worker, so a worker that refuses is refused to the client the same way."""
    try:
        principal = authorize(auth.scope_token(websocket.scope), deployment_id, "WS", path)
    except HTTPException:
        await websocket.close(code=1008)
        return
    if not await supervisor.wait_ready(deployment_id, START_WAIT):
        await websocket.close(code=1013)  # try again later
        return
    # The caller's ?token= isn't the worker's: it gets the service token in a header
    query = urlencode([(k, v) for k, v in parse_qsl(websocket.scope.get("query_string", b"").decode(), keep_blank_values=True)
                       if k != "token"])
    uri = f"ws://worker/{path}" + (f"?{query}" if query else "")
    headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in websocket.scope.get("headers", [])
               if k.decode("latin-1").lower() in WS_HEADERS]
    headers += [(k.decode(), v.decode()) for k, v in _upstream_headers(principal)]
    try:
        upstream = await websockets.unix_connect(str(registry.socket_path(deployment_id)), uri, extra_headers=headers,
                                                 open_timeout=10, ping_interval=None, max_size=None)
    except websockets.InvalidStatusCode as e:
        await websocket.close(code=1008 if e.status_code in (401, 403) else 1011)
        return
    except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as e:
        logger.warning("Websocket to %s/%s failed: %s", deployment_id, path, e)
        await websocket.close(code=1011)
        return
    await websocket.accept(subprotocol=upstream.subprotocol)

    async def client_to_upstream():
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                await upstream.close(code=message.get("code") or 1000)
                return
            if message.get("text") is not None:
                await upstream.send(message["text"])
            elif message.get("bytes") is not None:
                await upstream.send(message["bytes"])

    async def upstream_to_client():
        try:
            async for message in upstream:
                if isinstance(message, str):
                    await websocket.send_text(message)
                else:
                    await websocket.send_bytes(message)
        except websockets.ConnectionClosed:
            pass
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=upstream.close_code or 1000)

    tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        await upstream.close()
