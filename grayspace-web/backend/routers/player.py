"""The deployment's Player view (services/player.py): what the builder's Player step offers, and what
is applied. The view itself is the draft's player section (PUT /draft), deployed by the rollout.

    GET /api/v1/player              the view applied: its ID, team IDs and VMs
    GET /api/v1/player/templates    Player's application templates
    GET /api/v1/player/roles        the roles a team can have
    GET /api/v1/player/guests       the VMs teams can get: the base's, grouped, and the draft's enclaves'

503 when the deployment has no Player option (Settings).
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from services import player
from services.auth import require_token

router = APIRouter(prefix="/api/v1/player", tags=["player"], dependencies=[Depends(require_token)])

SIMULATED_TEMPLATES = [{"id": "e4698c62-7322-445b-946d-cafe1cf510bc", "name": "VMs", "url": "{vmUrl}?viewId={viewId}", "icon": "", "embeddable": True},
                       {"id": "5a2b7c1e-0000-4000-8000-000000000001", "name": "Map", "url": "{mapUrl}", "icon": "", "embeddable": True},
                       {"id": "5a2b7c1e-0000-4000-8000-000000000002", "name": "Chat", "url": "https://chat.example", "icon": "", "embeddable": False}]
SIMULATED_ROLES = [{"id": "r-member", "name": "View Member"}, {"id": "r-observer", "name": "Observer"}, {"id": "r-admin", "name": "View Admin"}]


class AppliedView(BaseModel):
    view: Optional[str] = None
    view_id: Optional[str] = None
    team_ids: Dict[str, str] = Field(default_factory=dict)
    guests: List[str] = Field(default_factory=list)
    applied: Optional[str] = None
    url: Optional[str] = Field(None, description="The view in Player's UI: the Crucible connection's Player API URL without /api, then /view/{view_id}")


class Template(BaseModel):
    id: str
    name: str
    url: Optional[str] = None
    icon: Optional[str] = None
    embeddable: Optional[bool] = None


class Role(BaseModel):
    id: str
    name: str


class Guests(BaseModel):
    base: List[Dict[str, Any]] = Field(..., description="{hostname, group: routers|core_services|management, isp, role, type}")
    enclaves: List[Dict[str, Any]] = Field(..., description="Per draft enclave: {prefix, slug, sites: [{name, edge, hint}], hosts: "
                                           "[{name, count, image, hint}]}; hint: the spec marks it crucible: true")


def _need_player():
    if not player.enabled():
        raise HTTPException(503, "This deployment has no Player view option; turn it on in its Settings")


def _crucible():
    from services import crucible_api, dev_simulation
    from services.slices import environment

    _need_player()
    return crucible_api, dev_simulation.enabled(), crucible_api.from_env(environment.job_env())


@router.get("", response_model=AppliedView)
async def get_view():
    _need_player()
    done = player.applied() or {}
    return AppliedView(**done, url=player.view_url(done.get("view_id")))


@router.get("/templates", response_model=List[Template], responses={502: {"description": "Crucible didn't answer"}})
async def get_templates():
    api, simulated, cfg = _crucible()
    if simulated:
        return SIMULATED_TEMPLATES
    try:
        found = await api.templates(cfg)
    except api.CrucibleError as e:
        raise HTTPException(502, str(e)) from None
    return [Template(id=t["id"], name=t.get("name") or t["id"], url=t.get("url"), icon=t.get("icon"), embeddable=t.get("embeddable"))
            for t in found if t.get("id")]


@router.get("/roles", response_model=List[Role], responses={502: {"description": "Crucible didn't answer"}})
async def get_roles():
    api, simulated, cfg = _crucible()
    if simulated:
        return SIMULATED_ROLES
    try:
        found = await api.team_roles(cfg)
    except api.CrucibleError as e:
        raise HTTPException(502, str(e)) from None
    return [Role(id=r["id"], name=r.get("name") or r["id"]) for r in found if r.get("id")]


@router.get("/guests", response_model=Guests)
async def get_guests(version: Optional[str] = Query(None, description="The draft's core version; the deployed one's guests when unset"),
                     scope: Optional[str] = Query(None)):
    """The VMs the Player step can assign: the core version's guests (the deployed tfvars, or the
    version's when it isn't deployed) and the saved draft's enclaves' sites and hosts."""
    from services import rollout

    _need_player()
    try:
        tfvars = player.version_tfvars(version, scope)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    return player.candidates(rollout.get_draft(), tfvars)
