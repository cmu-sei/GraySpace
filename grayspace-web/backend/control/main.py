"""The control plane's app: uvicorn control.main:app (docs/deployments.md).

    /api/v1/deployments[...]          the registry (control/api.py)
    /api/v1/auth, me, users, audit, nodes   sign-in, accounts and admin (control/accounts.py)
    /api/v1/platform[...]             the Proxmox and Crucible connections (control/platform_api.py)
    /deployments/{id}/{path}          the deployment's worker: {path} unchanged (HTTP and websockets)
    /api/apps                         the apps in the header's app switcher (settings.apps)
    /api/{path}, /ws/{path}           the default deployment's worker (deprecated alias)
    /health                           the control plane itself
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from control import accounts, api, audit, auth, platform, platform_api, proxy, registry, users
from control.models import CreateDeployment
from control.settings import SwitcherApp, settings
from control.supervisor import supervisor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("control")

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def prepare():
    """First start: the site file's connections, then adopt the existing single-deployment tree;
    deployments from before connections get a placement; then the bootstrap deployments."""
    settings.root.mkdir(parents=True, exist_ok=True)
    platform.import_site(logger.info)
    platform.seed_simulated()
    if not registry.all_records() and settings.adopt_path:
        record = registry.adopt(Path(settings.adopt_path))
        if record:
            logger.info("Adopted %s as deployment '%s' (the default)", settings.adopt_path, record["id"])
    platform.migrate_records(logger.info)
    users.prepare(logger.info)
    owner = settings.bootstrap_admin or next((name for name, role in settings.seed_user_list if role == "admin"), None)
    for deployment_id in settings.bootstrap_ids:
        if registry.get(deployment_id) is None:
            registry.create(CreateDeployment(id=deployment_id), owner=owner)
            logger.info("Created deployment '%s'", deployment_id)
    if owner and users.get(owner):
        for deployment_id in registry.adopt_owners(owner):
            logger.info("Deployment '%s' had no owner; it is now %s's", deployment_id, owner)


@asynccontextmanager
async def lifespan(app: FastAPI):
    prepare()
    supervisor.listeners.append(api.worker_changed)
    supervisor.start_all()
    logger.info("GraySpace control plane: %d deployment(s) in %s, default %s", len(registry.all_records()), settings.root,
                registry.default_id() or "none")
    yield
    await supervisor.stop_all()
    await proxy.close_clients()


app = FastAPI(title="GraySpace control plane", lifespan=lifespan, docs_url="/control/docs", openapi_url="/control/openapi.json")
# Audit is added first so CORS wraps it: preflights aren't writes, and 401s still carry CORS headers
app.add_middleware(audit.AuditMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"],
                   allow_headers=["*"])
app.include_router(api.router)
app.include_router(api.events_router)
app.include_router(accounts.auth_router)
app.include_router(accounts.me_router)
app.include_router(accounts.users_router)
app.include_router(accounts.admin_router)
app.include_router(platform_api.router)


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "healthy", "deployments": {r["id"]: supervisor.status(r["id"]).state for r in registry.all_records()}}


@app.get("/api/apps", response_model=List[SwitcherApp], response_model_exclude_none=True, tags=["shell"],
         dependencies=[Depends(auth.current)])
async def switcher_apps():
    """The switcher's apps in the configured order, this one included; url only where one is configured.
    Before the /api alias below, which would send it to the default deployment."""
    return settings.apps


@app.api_route("/deployments/{deployment_id}/{path:path}", methods=METHODS, include_in_schema=False)
async def deployment_http(request: Request, deployment_id: str, path: str):
    principal = proxy.authorize(auth.header_token(request.headers.raw), deployment_id, request.method, path)
    return await proxy.forward(request, deployment_id, path, principal)


@app.websocket("/deployments/{deployment_id}/{path:path}")
async def deployment_websocket(websocket: WebSocket, deployment_id: str, path: str):
    await proxy.relay(websocket, deployment_id, path)


@app.api_route("/api/{path:path}", methods=METHODS, include_in_schema=False)
async def default_http(request: Request, path: str):
    deployment_id = proxy.default_or_404()
    principal = proxy.authorize(auth.header_token(request.headers.raw), deployment_id, request.method, f"api/{path}")
    return await proxy.forward(request, deployment_id, f"api/{path}", principal, alias=True)


@app.websocket("/api/{path:path}")
async def default_api_websocket(websocket: WebSocket, path: str):
    await _default_websocket(websocket, f"api/{path}")


@app.websocket("/ws/{path:path}")
async def default_ws_websocket(websocket: WebSocket, path: str):
    await _default_websocket(websocket, f"ws/{path}")


async def _default_websocket(websocket: WebSocket, path: str):
    deployment_id = registry.default_id()
    if deployment_id is None:
        await websocket.close(code=1008)
        return
    await proxy.relay(websocket, deployment_id, path)
