"""What slices can use on a base version."""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from models.capabilities import AttachmentPoint, Capabilities
from services import base_versions, capabilities
from services.auth import require_token
from services.topology_service import topology_service

router = APIRouter(prefix="/api/v1", tags=["capabilities"], dependencies=[Depends(require_token)])

Scope = Optional[Literal["full", "tier1"]]


def _target(version: Optional[str], scope: Scope):
    """The base version and scope to describe: the deployed ones unless a version is named."""
    resolved = base_versions.resolve(version)
    if resolved is None:
        if version is not None:
            raise HTTPException(status_code=404, detail=f"No shipped core version '{version}'")
        raise HTTPException(
            status_code=409,
            detail="The deployed core version is unknown or not a shipped version; name one with ?version=",
        )
    if scope is None:
        deployed = topology_service.tfvars_data.get("base") or {}
        scope = deployed.get("scope", "full") if version is None else "full"
    name, directory = resolved
    return name, directory, base_versions.topology().load(directory), scope


def _is_deployed(directory) -> bool:
    deployed = (topology_service.tfvars_data.get("base") or {}).get("csv_sha256")
    return bool(deployed) and base_versions.topology().csv_sha256(directory) == deployed


@router.get("/capabilities", response_model=Capabilities)
async def get_capabilities(
    version: Optional[str] = Query(None, description="A shipped core version; defaults to the deployed one"),
    scope: Scope = Query(None, description="Defaults to the deployed scope, or full when a version is named"),
):
    """
    What a slice can use on a core version: the image catalog and roles, the slice features this
    GraySpace builds, whether it can run jobs, the management subnet and slice pool, DNS (TLDs,
    hosting servers, resolvers), CAs, ISPs and their locations, and the Proxmox node's capacity
    (CPU, memory and the block storage).
    """
    name, directory, topo, scope = _target(version, scope)
    return await capabilities.capabilities(name, directory, topo, scope)


@router.get("/attachment-points", response_model=List[AttachmentPoint])
async def get_attachment_points(
    isp: Optional[str] = Query(None, description="Only routers of this ISP"),
    location: Optional[str] = Query(None, description="Only routers in this location, e.g. Chicago"),
    version: Optional[str] = Query(None, description="A shipped core version; defaults to the deployed one"),
    scope: Scope = Query(None, description="Defaults to the deployed scope, or full when a version is named"),
):
    """
    Routers whose customer network slice sites and internet hosts can join: only routers with a
    customer network, with the static addresses free on it. On the deployed version, addresses
    live instances hold are not free. Also the free space in each ISP's public block, from which
    routed sites get their /24.
    """
    _, directory, topo, scope = _target(version, scope)
    return capabilities.attachment_points(
        topo, scope, isp=isp, location=location, deployed=_is_deployed(directory),
        dhcp_start=int(topology_service.tfvars_data.get("customer_dhcp_start") or 20),
    )
