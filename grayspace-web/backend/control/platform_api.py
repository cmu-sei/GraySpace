"""/api/v1/platform: the Proxmox and Crucible connections deployments use (control/platform.py).

    GET    /api/v1/platform                      both lists, secrets as names only (builders and admins)
    PUT    /api/v1/platform/defaults             the connection new deployments get
    POST   /api/v1/platform/proxmox              a Proxmox connection
    PATCH, DELETE /api/v1/platform/proxmox/{id}
    POST   /api/v1/platform/proxmox/{id}/test    the API, the credentials, each node's storage and bridges, SSH
    GET    /api/v1/platform/proxmox/{id}/discover  the cluster's nodes, storage, bridges and vnets
    POST   /api/v1/platform/crucible             a Crucible connection
    PATCH, DELETE /api/v1/platform/crucible/{id}
    POST   /api/v1/platform/crucible/{id}/test   a token from Keycloak, and the Player API accepting it

Changes need platform:manage (admins). A connection deployments use can't be deleted; its host,
port, or a used node's storage and bridges change only while those deployments have nothing
deployed. New credentials reach them at once: their environment files are written again and idle
workers restart.
"""
import asyncio
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException

from control import api, auth, platform, probes, registry
from control.models import (ConnectionChange, ConnectionCheck, ConnectionTest, CreateCrucibleConnection, CreateProxmoxConnection,
                            CrucibleConnectionFields, Discovery, Platform, PlatformDefaults, ProxmoxConnectionFields)
from control.supervisor import supervisor

router = APIRouter(prefix="/api/v1/platform", tags=["platform"], dependencies=[Depends(auth.current)])
manage = [Depends(auth.require("platform:manage"))]


def _call(fn, *args):
    try:
        return fn(*args)
    except platform.PlatformError as e:
        raise HTTPException(e.status, e.detail) from None


async def _apply(ids: List[str]) -> ConnectionChange:
    """The deployments' environment files again, and their workers restarted if no job runs."""
    await asyncio.to_thread(registry.rewrite_environments, ids)
    pending = []
    for deployment_id in ids:
        if not supervisor.running(deployment_id):
            continue
        _, rest = await api.summary(deployment_id, fresh=True)
        if rest is not None and rest.active_job is not None:
            pending.append(deployment_id)
            continue
        await supervisor.worker(deployment_id).restart()
        api.publish("deployment.updated", deployment_id)
    return ConnectionChange(restart_pending=pending)


@router.get("", response_model=Platform, dependencies=[Depends(auth.require("deployments:create"))])
async def get_platform():
    """The connections new deployments choose from. Secrets are named, never returned."""
    return platform.view()


@router.put("/defaults", response_model=Platform, dependencies=manage)
async def set_defaults(body: PlatformDefaults):
    _call(platform.set_default, body.default_proxmox)
    return platform.view()


@router.post("/proxmox", response_model=Platform, status_code=201, dependencies=manage)
async def create_proxmox(body: CreateProxmoxConnection):
    fields = body.model_dump(exclude={"id"})
    fields["nodes"] = [n.model_dump() for n in body.nodes]
    _call(platform.put_proxmox, body.id, fields, True)
    return platform.view()


@router.patch("/proxmox/{connection_id}", response_model=ConnectionChange, dependencies=manage,
              responses={409: {"description": "A deployment uses what would change, and has something deployed"}})
async def update_proxmox(connection_id: str, body: ProxmoxConnectionFields):
    """Fields left out stay; a secret sent empty is cleared."""
    before = platform.proxmox(connection_id)
    if before is None:
        raise HTTPException(404, f"No Proxmox connection '{connection_id}'")
    users = platform.view_users("proxmox", connection_id)
    fields = body.model_dump(exclude_unset=True)
    if body.nodes is not None:
        fields["nodes"] = [n.model_dump() for n in body.nodes]
        new = {n["name"]: {k: v for k, v in n.items() if v not in (None, "")} for n in fields["nodes"]}
        for old in before.get("nodes") or []:
            used = platform.users_of_node(connection_id, old["name"])
            if not used:
                continue
            if old["name"] not in new:
                raise HTTPException(409, f"Deployments run on {old['name']}: {', '.join(used)}")
            if new[old["name"]] != {k: v for k, v in old.items() if v not in (None, "")}:
                for deployment_id in used:
                    await api._nothing_deployed(registry.get(deployment_id), f"change the storage or bridges of {old['name']}")
    if any(k in fields and fields[k] != before.get(k, 8006 if k == "port" else None) for k in ("host", "port")):
        for deployment_id in users:
            await api._nothing_deployed(registry.get(deployment_id), f"move {connection_id} to another host")
    _call(platform.put_proxmox, connection_id, fields, False)
    return await _apply(users)


@router.delete("/proxmox/{connection_id}", status_code=204, dependencies=manage)
async def delete_proxmox(connection_id: str):
    _call(platform.delete_proxmox, connection_id)


@router.post("/proxmox/{connection_id}/test", response_model=ConnectionTest, dependencies=manage)
async def test_proxmox(connection_id: str):
    conn = platform.proxmox(connection_id)
    if conn is None:
        raise HTTPException(404, f"No Proxmox connection '{connection_id}'")
    checks = [ConnectionCheck(name=n, status=s, message=m) for n, s, m in await probes.check_proxmox(conn)]
    return ConnectionTest(ok=not any(c.status == "error" for c in checks), checks=checks)


@router.get("/proxmox/{connection_id}/discover", response_model=Discovery, dependencies=manage,
            responses={502: {"description": "Proxmox didn't answer, or refused the credentials"}})
async def discover_proxmox(connection_id: str):
    conn = platform.proxmox(connection_id)
    if conn is None:
        raise HTTPException(404, f"No Proxmox connection '{connection_id}'")
    try:
        return await probes.discover(conn)
    except (probes.ProbeError, httpx.HTTPError, ValueError, KeyError) as e:
        raise HTTPException(502, f"Proxmox at {conn['host']}: {e}") from None


@router.post("/crucible", response_model=Platform, status_code=201, dependencies=manage)
async def create_crucible(body: CreateCrucibleConnection):
    _call(platform.put_crucible, body.id, body.model_dump(exclude={"id"}), True)
    return platform.view()


@router.patch("/crucible/{connection_id}", response_model=ConnectionChange, dependencies=manage)
async def update_crucible(connection_id: str, body: CrucibleConnectionFields):
    users = platform.view_users("crucible", connection_id)
    _call(platform.put_crucible, connection_id, body.model_dump(exclude_unset=True), False)
    return await _apply(users)


@router.delete("/crucible/{connection_id}", status_code=204, dependencies=manage)
async def delete_crucible(connection_id: str):
    _call(platform.delete_crucible, connection_id)


@router.post("/crucible/{connection_id}/test", response_model=ConnectionTest, dependencies=manage)
async def test_crucible(connection_id: str):
    from services import crucible_api

    conn = platform.crucible(connection_id)
    if conn is None:
        raise HTTPException(404, f"No Crucible connection '{connection_id}'")
    missing = [k for k in ("token_url", "client_id", "username", "player_api_url", *platform.CRUCIBLE_SECRETS) if not conn.get(k)]
    if missing:
        return ConnectionTest(ok=False, checks=[ConnectionCheck(name="settings", status="error", message="Not set: " + ", ".join(missing))])
    checks = [ConnectionCheck(name="crucible", status=s, message=m) for s, m in await crucible_api.check(conn)]
    if not conn.get("service_user_id"):
        checks.append(ConnectionCheck(name="settings", status="warning", message="No service user: views get no admin"))
    return ConnectionTest(ok=not any(c.status == "error" for c in checks), checks=checks)
