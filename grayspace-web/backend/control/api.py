"""/api/v1/deployments: the deployments this GraySpace runs (docs/deployments.md)."""
import asyncio
import contextlib
import json
import time
from typing import Dict, List, Optional, Set, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, WebSocket, WebSocketDisconnect

from control import access, auth, platform, registry, users
from control.access import Principal
from control.models import (
    BaseSummary,
    CreateDeployment,
    Deployment,
    DeploymentDetail,
    DeploymentSummary,
    Members,
    UpdateDeployment,
    secret_names,
)
from control.settings import settings
from control.supervisor import client, log_tail, supervisor

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"], dependencies=[Depends(auth.current)])
events_router = APIRouter(tags=["deployments"])

SUMMARY_TTL = 10.0
_summary_cache: Dict[str, Tuple[float, BaseSummary, Optional[DeploymentSummary]]] = {}
_refreshing: Dict[str, asyncio.Task] = {}


async def worker_get(deployment_id: str, path: str, timeout: float = 5.0, params: Optional[Dict] = None):
    headers = {"Authorization": f"Bearer {settings.api_token}"} if settings.api_token else {}
    async with client(deployment_id, timeout=timeout) as c:
        r = await c.get(path, headers=headers, params=params)
        r.raise_for_status()
        return r.json()


async def summary(deployment_id: str, fresh: bool = False) -> Tuple[Optional[BaseSummary], Optional[DeploymentSummary]]:
    """The base and the rest of the summary; (None, None) when the worker isn't running. Older than
    SUMMARY_TTL, the cached one is returned at once and refreshed in the background, which publishes
    deployment.updated if it changed. fresh, or nothing cached, waits for the worker."""
    if not supervisor.running(deployment_id):
        return None, None
    cached = _summary_cache.get(deployment_id)
    if cached and not fresh:
        if time.monotonic() - cached[0] >= SUMMARY_TTL and deployment_id not in _refreshing:
            _refreshing[deployment_id] = asyncio.create_task(_refresh(deployment_id))
        return cached[1], cached[2]
    return await _fetch(deployment_id)


async def _refresh(deployment_id: str):
    try:
        before = _summary_cache.get(deployment_id)
        after = await _fetch(deployment_id)
        if before is None or (before[1], before[2]) != after:
            publish("deployment.updated", deployment_id)
    finally:
        _refreshing.pop(deployment_id, None)


async def _fetch(deployment_id: str) -> Tuple[BaseSummary, Optional[DeploymentSummary]]:
    rest = None
    try:
        body = await worker_get(deployment_id, "/api/v1/summary")
        base = body.get("base") or {}
        found = BaseSummary(status=base.get("status") or "unknown", version=base.get("version"), scope=base.get("scope"),
                            routers=base.get("routers"))
        rest = DeploymentSummary(**{k: v for k, v in body.items() if k in DeploymentSummary.model_fields})
    except httpx.HTTPStatusError as e:
        found = await _base_only(deployment_id) if e.response.status_code == 404 else BaseSummary(status="unknown")
    except (httpx.HTTPError, OSError, ValueError):
        found = BaseSummary(status="unknown")
    _summary_cache[deployment_id] = (time.monotonic(), found, rest)
    return found, rest


async def _base_only(deployment_id: str) -> BaseSummary:
    """A worker from before /api/v1/summary (an adopted tree on older code)."""
    try:
        body = await worker_get(deployment_id, "/api/v1/base")
        return BaseSummary(status=body.get("status") or "unknown", version=body.get("version"))
    except (httpx.HTTPError, OSError, ValueError):
        return BaseSummary(status="unknown")


async def base_summary(deployment_id: str, fresh: bool = False) -> Optional[BaseSummary]:
    return (await summary(deployment_id, fresh))[0]


def _view(record: Dict, base: Optional[BaseSummary], rest: Optional[DeploymentSummary], principal: Principal) -> Dict:
    secret = registry.secrets(record["id"])
    env = {}
    with contextlib.suppress(Exception):
        env = registry.effective_env(record)
    if record["kind"] == "managed":
        placement, proxmox = record.get("placement"), platform.resolved(record) or _from_env(env)
    else:
        placement, proxmox = platform.placement_for_host(env.get("TF_VAR_proxmox_host_addr")), _from_env(env)
    return dict(
        id=record["id"], name=record.get("name") or record["id"], description=record.get("description", ""), kind=record["kind"],
        default=registry.default_id() == record["id"], created=record.get("created", ""), url=f"/deployments/{record['id']}",
        placement=placement, proxmox=proxmox, options=record.get("options") or {},
        secrets=secret_names(secret.get("credentials"), secret.get("router_password")),
        worker=supervisor.status(record["id"]), base=base, summary=rest, owner=record.get("owner"),
        members=record.get("members") or [], my_access=access.permissions(principal, record), warnings=registry.warnings(record, env),
    )


def _from_env(env: Dict[str, str]) -> Dict:
    """Where a deployment without a connection runs: its environment file's settings."""
    names = {"host_addr": "TF_VAR_proxmox_host_addr", "node": "TF_VAR_proxmox_node", "blockstore": "TF_VAR_proxmox_blockstore",
             "filestore": "TF_VAR_proxmox_filestore", "mgmt_bridge": "TF_VAR_proxmox_mgmt_bridge", "vm_bridge": "TF_VAR_proxmox_vm_bridge"}
    return {k: env[v] for k, v in names.items() if env.get(v)}


async def view(record: Dict, principal: Principal, fresh: bool = False) -> Deployment:
    base, rest = await summary(record["id"], fresh)
    return Deployment(**_view(record, base, rest, principal))


def visible(principal: Principal, record: Optional[Dict]) -> bool:
    return record is not None and access.access(principal, record) is not None


def _get(deployment_id: str, principal: Principal, permission: str = "read") -> Dict:
    """The record, or 404 when it doesn't exist or isn't the caller's to see; 403 without the permission."""
    record = registry.get(deployment_id)
    if not visible(principal, record):
        raise HTTPException(404, f"No deployment '{deployment_id}'")
    if permission not in access.permissions(principal, record):
        raise HTTPException(403, f"You need {permission} on deployment '{deployment_id}'")
    return record


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except registry.RegistryError as e:
        raise HTTPException(e.status, e.detail) from None


async def _nothing_deployed(record: Dict, action: str):
    """409 unless the deployment has no base, no live enclave instances and no Terraform resources."""
    deployment_id = record["id"]
    if settings.simulate:
        return  # a simulated deployment has nothing on Proxmox
    state = registry.work_dir(record) / "terraform" / "terraform.tfstate"
    with contextlib.suppress(OSError, ValueError):
        if json.loads(state.read_text()).get("resources"):
            raise HTTPException(409, f"Deployment '{deployment_id}' has Terraform resources; destroy its base first "
                                     f"(DELETE /deployments/{deployment_id}/api/v1/base) before you {action}")
    if not supervisor.running(deployment_id) and not await supervisor.wait_ready(deployment_id, 30):
        raise HTTPException(409, f"Deployment '{deployment_id}' isn't running, so whether anything is deployed can't be checked; "
                                 f"restart it (POST /api/v1/deployments/{deployment_id}/restart)")
    try:
        base = await worker_get(deployment_id, "/api/v1/base", timeout=30)
        instances = await worker_get(deployment_id, "/api/v1/instances", timeout=30)
    except (httpx.HTTPError, OSError, ValueError) as e:
        raise HTTPException(409, f"Deployment '{deployment_id}' didn't say what it has deployed ({e})") from None
    if instances:
        raise HTTPException(409, f"Deployment '{deployment_id}' has {len(instances)} live enclave instance(s); destroy them before you {action}")
    if base.get("status") != "not_deployed":
        raise HTTPException(409, f"Deployment '{deployment_id}' has a base ({base.get('status')}); destroy it first "
                                 f"(DELETE /deployments/{deployment_id}/api/v1/base) before you {action}")


async def _no_player_view(record: Dict):
    """409 while the deployment has a Player view: the rollout removes it (a draft without player)."""
    try:
        view = await worker_get(record["id"], "/api/v1/player", timeout=15)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 503:
            return  # the worker has no Player option already
        raise HTTPException(409, f"Deployment '{record['id']}' didn't say whether it has a Player view ({e})") from None
    except (httpx.HTTPError, OSError, ValueError) as e:
        raise HTTPException(409, f"Deployment '{record['id']}' didn't say whether it has a Player view ({e})") from None
    if view.get("view_id"):
        raise HTTPException(409, f"Deployment '{record['id']}' has the Player view {view.get('view')}; remove it first: deploy its draft "
                                 "without a Player view")


@router.get("", response_model=List[Deployment])
async def list_deployments(principal: Principal = Depends(auth.current)):
    """The deployments you own or are a member of; every one for admins."""
    records = [r for r in registry.all_records() if visible(principal, r)]
    found = await asyncio.gather(*(summary(r["id"]) for r in records))
    return [Deployment(**_view(r, base, rest, principal)) for r, (base, rest) in zip(records, found, strict=True)]


@router.post("", response_model=Deployment, status_code=201,
             responses={403: {"description": "Only builders and admins create deployments"}, 409: {"description": "The ID is taken"}})
async def create_deployment(body: CreateDeployment, principal: Principal = Depends(auth.require("deployments:create"))):
    """Create a deployment: its directory, environment file and work tree, and start its worker.
    You become its owner. Its Terrain API is then at /deployments/{id}/api/v1; deploy its base with
    POST /deployments/{id}/api/v1/base/deploy."""
    if body.default and not principal.can("deployments:all"):
        raise HTTPException(403, "Only admins choose the default deployment")
    owner = principal.username if principal.kind == "user" else None
    record = await asyncio.to_thread(_call, registry.create, body, owner=owner)
    supervisor.start(record["id"])
    publish("deployment.created", record["id"])
    return await view(record, principal)


@router.get("/{deployment_id}", response_model=DeploymentDetail)
async def get_deployment(deployment_id: str, principal: Principal = Depends(auth.current)):
    record = _get(deployment_id, principal)
    base, rest = await summary(deployment_id)
    data = _view(record, base, rest, principal)
    return DeploymentDetail(**data, work_dir=str(registry.work_dir(record)), log=log_tail(deployment_id))


@router.patch("/{deployment_id}", response_model=Deployment)
async def update_deployment(deployment_id: str, body: UpdateDeployment, principal: Principal = Depends(auth.current)):
    """name and description change any time; placement (a connection and node), options and
    router_password only while nothing is deployed, and the worker restarts with them (owners and
    admins). default: admins. proxmox and credentials are the connection's: refused."""
    record = _get(deployment_id, principal, "members:manage")
    if body.default is not None and not principal.can("deployments:all"):
        raise HTTPException(403, "Only admins choose the default deployment")
    _call(registry.refuse_legacy, body)
    settings_change = any(v is not None for v in (body.placement, body.options, body.router_password))
    options = body.options.model_dump(exclude_none=True) if body.options is not None else {}
    only_player = body.placement is None and body.router_password is None and set(options) <= {"player", "player_connection"}
    if options.get("player") is False and (record.get("options") or {}).get("player"):
        await _no_player_view(record)
    if settings_change and not only_player:
        await _nothing_deployed(record, "change where it runs")
    record = await asyncio.to_thread(_call, registry.update, deployment_id, body, settings_change)
    if settings_change:
        await supervisor.worker(deployment_id).restart()
    publish("deployment.updated", deployment_id)
    return await view(record, principal)


@router.put("/{deployment_id}/members", response_model=Deployment,
            responses={422: {"description": "An unknown user, the owner, or build access for a user"}})
async def set_members(deployment_id: str, body: Members, principal: Principal = Depends(auth.current)):
    """Who else may see and use the deployment: build access (builders only) or use access.
    Replaces the list. Owners and admins."""
    record = _get(deployment_id, principal, "members:manage")
    seen = set()
    for member in body.members:
        user = users.get(member.user)
        if user is None:
            raise HTTPException(422, f"No user '{member.user}'")
        if member.user == record.get("owner"):
            raise HTTPException(422, f"'{member.user}' owns the deployment")
        if member.access == "build" and user["role"] == "user":
            raise HTTPException(422, f"'{member.user}' is a user: only builders get build access")
        if member.user in seen:
            raise HTTPException(422, f"'{member.user}' is listed twice")
        seen.add(member.user)
    record = await asyncio.to_thread(_call, registry.set_members, deployment_id, [m.model_dump() for m in body.members])
    publish("deployment.updated", deployment_id)
    return await view(record, principal)


@router.post("/{deployment_id}/restart", response_model=Deployment, status_code=202)
async def restart_deployment(deployment_id: str, principal: Principal = Depends(auth.current)):
    """Restart the deployment's worker. A managed deployment's work tree is refreshed from the
    server's code first. Jobs running in it fail, as on any backend restart. Needs build."""
    record = _get(deployment_id, principal, "build")
    await supervisor.worker(deployment_id).restart()
    return await view(record, principal)


@router.delete("/{deployment_id}", status_code=204, responses={409: {"description": "Something is still deployed"}})
async def delete_deployment(deployment_id: str, principal: Principal = Depends(auth.current)):
    """Remove a deployment that has nothing deployed. A managed deployment's directory is deleted;
    an adopted tree is only forgotten. Owners and admins."""
    record = _get(deployment_id, principal, "delete")
    await _nothing_deployed(record, "remove the deployment")
    await supervisor.stop(deployment_id)
    _summary_cache.pop(deployment_id, None)
    await asyncio.to_thread(registry.remove, deployment_id)
    publish("deployment.deleted", deployment_id, record)
    return Response(status_code=204)


# Events: deployments created, changed, removed, and their workers' state, for the UI's switcher.
_subscribers: Dict[asyncio.Queue, Principal] = {}


def publish(kind: str, deployment_id: str, removed: Optional[Dict] = None):
    """removed: a deleted deployment's last record, to tell who could see it."""
    record = registry.get(deployment_id)
    cached = _summary_cache.get(deployment_id)
    for queue, principal in list(_subscribers.items()):
        if not visible(principal, record or removed):
            continue
        message = {"type": kind, "id": deployment_id,
                   "deployment": json.loads(Deployment(**_view(record, cached[1] if cached else None, cached[2] if cached else None,
                                                               principal)).model_dump_json()) if record else None}
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(message)


def worker_changed(deployment_id: str):
    publish("deployment.worker", deployment_id)


@events_router.websocket("/api/v1/deployments/events")
async def deployment_events(websocket: WebSocket):
    """Authenticate with an Authorization: Bearer header, or ?token= where headers can't be set.
    Only the deployments you can see."""
    principal, error = auth.problem(auth.scope_token(websocket.scope))
    if error:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subscribers[queue] = principal
    getter = receiver = None
    try:
        await websocket.send_json({"type": "hello", "schema": "grayspace-deployments-events-v1"})
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
        _subscribers.pop(queue, None)
        for task in (getter, receiver):
            if task is not None:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
