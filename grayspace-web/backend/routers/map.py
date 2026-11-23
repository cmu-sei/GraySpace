"""The map layer."""
import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from models.map import FeatureCollection
from services import base_versions, map_layer
from services.auth import require_token
from services.proxmox_client import proxmox_client
from services.slices import store
from services.topology_service import topology_service

router = APIRouter(prefix="/api/v1", tags=["map"], dependencies=[Depends(require_token)])


def _isp_order(name: Optional[str] = None):
    """ISPs in the core version's order (the deployed one's by default), so colors are the same on every map."""
    resolved = base_versions.resolve(name)
    if resolved is None:
        return []
    return [i.id for i in base_versions.topology().load(resolved[1]).of("ISP")]


@router.get("/base/preview", response_model=FeatureCollection, responses={404: {"description": "No such shipped version"}})
async def get_base_preview(version: str = Query(..., description="A shipped core version, e.g. grayspace/2026.11"),
                           scope: Literal["full", "tier1"] = Query("full")):
    """
    The map of a shipped core version as it would deploy, before it is: GET /map's routers and links
    for that version and scope, with no state (every status and link state unknown) and no enclaves.
    For comparing versions in the builder.
    """
    from services.slices import service

    if base_versions.resolve(version) is None:
        raise HTTPException(status_code=404, detail=f"No shipped core version '{version}'")
    try:
        tfvars = await asyncio.to_thread(service.shipped_tfvars, version, scope)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    order = await asyncio.to_thread(_isp_order, version)
    return map_layer.build(tfvars, order, {}, None, [])


@router.get("/map", response_model=FeatureCollection)
async def get_map(instance: Optional[str] = Query(None, description="Only this instance's slice features")):
    """
    The map as GeoJSON (RFC 7946) with current state: routers (positions already fanned out per
    city), links (great-circle LineStrings, split at the 180° meridian), and slice sites, internet
    hosts and their attachments. Colors and states are properties, so every map draws the same
    thing; updates arrive by feature id over /api/v1/events (feature.update) and /ws.
    """
    from tasks.state_monitor import state_monitor

    containers = await asyncio.to_thread(proxmox_client.get_deployment_containers)
    status = {c["name"]: c.get("status", "unknown") for c in containers} if containers else None
    order = await asyncio.to_thread(_isp_order)
    return map_layer.build(topology_service.tfvars_data, order, state_monitor.previous_link_states, status,
                           store.live_instances(), instance=instance)
