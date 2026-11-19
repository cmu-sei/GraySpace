"""Sign-in, the signed-in user, local accounts, the audit log and the Proxmox nodes (docs/deployments.md, Sign-in).

    /api/v1/auth/providers           which sign-in sources are on (no token needed)
    /api/v1/auth/login, logout       a session for a local account
    /api/v1/auth/password            change your own password
    /api/v1/auth/sessions[/{id}]     your sessions
    /api/v1/me                       who you are and what you may do
    /api/v1/users[...]               local accounts (admins)
    /api/v1/directory                who can be added to a deployment (builders and admins)
    /api/v1/audit                    every write and sign-in (admins)
    /api/v1/nodes                    the Proxmox nodes the deployments run on (admins)
"""
import asyncio
import contextlib
import fcntl
from typing import Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from control import api, audit, auth, registry, users
from control.access import Principal
from control.models import (
    AuditEntry,
    Capacity,
    CreateUser,
    DirectoryEntry,
    LoginRequest,
    LoginResponse,
    Me,
    Node,
    OneTimePassword,
    PasswordChange,
    Providers,
    SessionInfo,
    UpdateUser,
    User,
)
from control.settings import settings
from control.supervisor import supervisor

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
me_router = APIRouter(tags=["auth"])
users_router = APIRouter(prefix="/api/v1/users", tags=["users"], dependencies=[Depends(auth.require("users:manage"))])
admin_router = APIRouter(prefix="/api/v1", tags=["admin"])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except users.UserError as e:
        raise HTTPException(e.status, e.detail) from None


def _user(record: Dict) -> User:
    return User(**{k: v for k, v in users.public(record).items() if k in User.model_fields})


def _session(session: Dict, current: Optional[str] = None) -> SessionInfo:
    return SessionInfo(**{k: session[k] for k in ("id", "created", "last_used", "expires", "ip", "agent")}, current=session["id"] == current)


# ----- Sign-in -----

@auth_router.get("/providers", response_model=Providers)
async def providers():
    return Providers(providers=settings.provider_list)


@auth_router.post("/login", response_model=LoginResponse,
                  responses={401: {"description": "Wrong username or password, or the account is disabled"},
                             429: {"description": "Too many failed sign-ins; Retry-After says when to try again"}})
async def login(body: LoginRequest, request: Request):
    """A session for a local account. Send its token as Authorization: Bearer <token>."""
    ip = auth.client_ip(request)
    keys = [f"user:{body.username}", f"ip:{ip}"]
    if "local" not in settings.provider_list:
        raise HTTPException(403, "Local accounts are turned off on this server (GRAYSPACE_AUTH_PROVIDERS)")
    wait = auth.throttle.wait(keys)
    if wait:
        audit.record(body.username, "POST", request.url.path, 429, detail="throttled", ip=ip)
        raise HTTPException(429, f"Too many failed sign-ins; try again in {wait} s", headers={"Retry-After": str(wait)})
    record = await asyncio.to_thread(users.authenticate, body.username, body.password)
    if record is None or record.get("disabled"):
        auth.throttle.failed(keys)
        audit.record(body.username, "POST", request.url.path, 401, detail="disabled" if record else "wrong username or password", ip=ip)
        raise HTTPException(401, "Wrong username or password" if record is None else "This account is disabled; ask an admin")
    auth.throttle.succeeded(keys[:1])
    token, session = await asyncio.to_thread(users.create_session, record["username"], ip, request.headers.get("user-agent", ""))
    audit.record(record["username"], "POST", request.url.path, 200, detail="signed in", ip=ip)
    return LoginResponse(token=token, session=_session(session, session["id"]), must_change_password=bool(record.get("must_change_password")))


@auth_router.post("/logout", status_code=204)
async def logout(principal: Principal = Depends(auth.current_pending)):
    if principal.session_id:
        users.end_session(principal.session_id)
    return Response(status_code=204)


@auth_router.post("/password", status_code=204, responses={403: {"description": "The current password is wrong"}})
async def change_password(body: PasswordChange, principal: Principal = Depends(auth.current_pending)):
    """Change your own password. Your other sessions end."""
    if principal.kind != "user":
        raise HTTPException(400, "The service token has no password; change GRAYSPACE_API_TOKEN in the environment file")
    record = await asyncio.to_thread(users.authenticate, principal.username, body.current_password)
    if record is None:
        raise HTTPException(403, "The current password is wrong")
    if body.new_password == body.current_password:
        raise HTTPException(422, "Choose a password other than the current one")
    await asyncio.to_thread(_call, users.set_password, principal.username, body.new_password)
    users.end_sessions(principal.username, keep=principal.session_id)
    return Response(status_code=204)


@auth_router.get("/sessions", response_model=List[SessionInfo])
async def my_sessions(principal: Principal = Depends(auth.current)):
    return [_session(s, principal.session_id) for s in users.user_sessions(principal.username)]


@auth_router.delete("/sessions/{session_id}", status_code=204)
async def end_my_session(session_id: str, principal: Principal = Depends(auth.current)):
    if not users.end_session(session_id, principal.username):
        raise HTTPException(404, f"No session '{session_id}'")
    return Response(status_code=204)


@me_router.get("/api/v1/me", response_model=Me)
async def me(principal: Principal = Depends(auth.current_pending)):
    """Who you are, your role and server-wide permissions. A deployment's permissions are its my_access."""
    record = users.get(principal.username) if principal.kind == "user" else None
    return Me(user=_user(record) if record else None, kind=principal.kind, role=principal.role, permissions=principal.permissions,
              must_change_password=principal.must_change_password, providers=settings.provider_list)


# ----- Who can be a member -----

@admin_router.get("/directory", response_model=List[DirectoryEntry], dependencies=[Depends(auth.require("deployments:create"))])
async def directory():
    """The accounts a deployment's owner can add as members: username, name and role, without the rest. Disabled ones are left out."""
    return [DirectoryEntry(username=u["username"], name=u.get("name") or u["username"], role=u["role"]) for u in users.all_users() if not u.get("disabled")]


# ----- Local accounts -----

@users_router.get("", response_model=List[User])
async def list_users():
    return [_user(u) for u in users.all_users()]


@users_router.post("", response_model=OneTimePassword, status_code=201, responses={409: {"description": "The username is taken"}})
async def create_user(body: CreateUser):
    """A local account with a one-time password, shown once; the user changes it at first sign-in.
    deployments: add them to these with use access."""
    for deployment_id in body.deployments:
        if registry.get(deployment_id) is None:
            raise HTTPException(422, f"No deployment '{deployment_id}'")
    record, one_time = await asyncio.to_thread(_call, users.create, body.username, body.role, body.name, body.email)
    for deployment_id in body.deployments:
        existing = registry.get(deployment_id)
        members = [m for m in existing.get("members") or [] if m.get("user") != body.username]
        registry.set_members(deployment_id, members + [{"user": body.username, "access": "use"}])
        api.publish("deployment.updated", deployment_id)
    return OneTimePassword(user=_user(record), one_time_password=one_time)


@users_router.get("/{username}", response_model=User)
async def get_user(username: str):
    record = users.get(username)
    if record is None:
        raise HTTPException(404, f"No user '{username}'")
    return _user(record)


@users_router.patch("/{username}", response_model=User)
async def update_user(username: str, body: UpdateUser, principal: Principal = Depends(auth.current)):
    """Name, email, role, and disabling (their sessions end). You can't demote or disable yourself."""
    fields = body.model_dump(exclude_none=True)
    if username == principal.username and (fields.get("disabled") or fields.get("role", "admin") != "admin"):
        raise HTTPException(409, "You can't disable or demote yourself; ask another admin")
    return _user(await asyncio.to_thread(_call, users.update, username, **fields))


@users_router.delete("/{username}", status_code=204)
async def delete_user(username: str, principal: Principal = Depends(auth.current)):
    if username == principal.username:
        raise HTTPException(409, "You can't delete yourself; ask another admin")
    await asyncio.to_thread(_call, users.delete, username)
    await asyncio.to_thread(registry.remove_member, username)
    return Response(status_code=204)


@users_router.post("/{username}/reset-password", response_model=OneTimePassword)
async def reset_password(username: str):
    """A new one-time password, shown once. Their sessions end; they change it at next sign-in."""
    one_time = await asyncio.to_thread(_call, users.reset_password, username)
    return OneTimePassword(user=_user(users.get(username)), one_time_password=one_time)


# ----- The audit log and nodes -----

@admin_router.get("/audit", response_model=List[AuditEntry], dependencies=[Depends(auth.require("audit:read"))])
async def get_audit(deployment: Optional[str] = Query(None), user: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=5000)):
    """Every write through the server and every sign-in, newest first."""
    return await asyncio.to_thread(audit.entries, deployment, user, limit)


def _locked(host: Optional[str]) -> bool:
    """Whether a job holds the cluster's lock: try a shared lock without waiting, and let go at once."""
    with contextlib.suppress(OSError):
        with open(registry.lock_file(host), "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(f, fcntl.LOCK_UN)
    return False


async def _worker_summary(deployment_id: str) -> Optional[Dict]:
    if not supervisor.running(deployment_id):
        return None
    try:
        return await api.worker_get(deployment_id, "/api/v1/summary", timeout=15, params={"capacity": "true"})
    except (httpx.HTTPError, OSError, ValueError):
        return None


@admin_router.get("/nodes", response_model=List[Node], dependencies=[Depends(auth.require("nodes:read"))])
async def get_nodes():
    """The Proxmox nodes: every node of every connection (Admin > Platform), and where adopted trees
    run; their capacity, which deployments, and whether a job holds the cluster's lock."""
    from control import platform, probes

    groups: Dict[tuple, List[str]] = {}
    hosts: Dict[tuple, Optional[str]] = {}
    for c in platform.view()["proxmox"]:
        for n in c["nodes"]:
            groups.setdefault((c["id"], n["name"]), [])
            hosts[(c["id"], n["name"])] = c["host"]
    for record in registry.all_records():
        placement = record.get("placement") or {}
        conn = platform.proxmox(placement.get("connection")) if record["kind"] == "managed" else None
        if conn is not None:
            key = (conn["id"], (platform.node(conn, placement.get("node")) or {}).get("name"))
            hosts[key] = conn["host"]
        else:
            env = {}
            with contextlib.suppress(Exception):
                env = registry.effective_env(record)
            found = platform.placement_for_host(env.get("TF_VAR_proxmox_host_addr"))
            key = (found["connection"] if found else None, env.get("TF_VAR_proxmox_node"))
            hosts.setdefault(key, env.get("TF_VAR_proxmox_host_addr"))
        groups.setdefault(key, []).append(record["id"])
    ids = [i for members in groups.values() for i in members]
    found = dict(zip(ids, await asyncio.gather(*(_worker_summary(i) for i in ids)), strict=True))

    async def capacity(key, members):
        reported = next((found[i]["capacity"] for i in members if found[i] and found[i].get("capacity")), None)
        if reported is None and key[0] is not None:
            conn = platform.proxmox(key[0])
            node = platform.node(conn, key[1]) if conn else None
            reported = await probes.capacity(conn, node) if node else None
        return Capacity(**{k: v for k, v in reported.items() if k in Capacity.model_fields}) if reported else None

    keys = list(groups)
    capacities = await asyncio.gather(*(capacity(k, groups[k]) for k in keys))
    names = {c["id"]: c["name"] for c in platform.view()["proxmox"]}
    nodes = []
    for (connection, node), cap in zip(keys, capacities, strict=True):
        members = groups[(connection, node)]
        holder = next((i for i in members if found[i] and (found[i].get("active_job") or {}).get("state") == "running"), None)
        host = hosts.get((connection, node))
        nodes.append(Node(connection=connection, connection_name=names.get(connection), host=host, node=node, deployments=members,
                          locked=_locked(host), lock_holder=holder, capacity=cap))
    return nodes
