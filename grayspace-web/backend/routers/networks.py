"""Network (vNET/Zone) API endpoints."""
from fastapi import APIRouter, HTTPException
from typing import List
from models.network import VNetInfo, ZoneInfo, VNetZoneChangeRequest, LinkNetworkInfo, SDNApplyResult
from services.network_service import network_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/networks", tags=["networks"])


@router.get("/zones", response_model=List[ZoneInfo])
def get_zones():
    """
    Get all SDN zones for this deployment.

    Returns:
        List of zones with type and configuration info.
    """
    try:
        zones = network_service.get_zones()
        return zones
    except Exception as e:
        logger.error(f"Error retrieving zones: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vnets", response_model=List[VNetInfo])
def get_vnets():
    """
    Get all SDN vNETs for this deployment.

    Returns:
        List of vNETs with zone assignments.
    """
    try:
        vnets = network_service.get_vnets()
        return vnets
    except Exception as e:
        logger.error(f"Error retrieving vNETs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vnet/{vnet_id}", response_model=VNetInfo)
def get_vnet(vnet_id: str):
    """
    Get information about a specific vNET.

    Args:
        vnet_id: vNET identifier.

    Returns:
        vNET information.
    """
    vnet = network_service.get_vnet(vnet_id)
    if not vnet:
        raise HTTPException(
            status_code=404,
            detail=f"vNET {vnet_id} not found or not in this deployment"
        )
    return vnet


@router.put("/vnet/{vnet_id}/zone", response_model=SDNApplyResult)
def change_vnet_zone(vnet_id: str, request: VNetZoneChangeRequest):
    """
    Change a vNET's zone assignment.

    This will move the vNET to a different zone, potentially changing
    its network connectivity (e.g., from isolated to externally accessible).

    Args:
        vnet_id: vNET identifier.
        request: Zone change request with target zone_id.

    Returns:
        Result with success status and any warnings.
    """
    try:
        result = network_service.change_vnet_zone(vnet_id, request.zone_id)
        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing vNET zone: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/link/{link_id}", response_model=LinkNetworkInfo)
def get_link_network_info(link_id: str):
    """
    Get network information for a router-to-router link.

    Args:
        link_id: Link identifier (format: "router1-router2").

    Returns:
        Network information including vNET and zone details.
    """
    info = network_service.get_link_network_info(link_id)
    if not info:
        raise HTTPException(
            status_code=404,
            detail=f"Link {link_id} not found"
        )
    return info


@router.post("/apply")
def apply_sdn_config():
    """
    Apply pending SDN configuration changes.

    This should be called after making changes to zones or vNETs
    to activate the new configuration.

    Returns:
        Success message.
    """
    try:
        success = network_service._apply_sdn_config()
        if not success:
            raise HTTPException(
                status_code=500,
                detail="Failed to apply SDN configuration"
            )
        return {"status": "success", "message": "SDN configuration applied"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying SDN config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
