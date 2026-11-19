"""Interface zone API endpoints (for customer-facing interfaces only)."""
from fastapi import APIRouter, HTTPException
from typing import List
from models.network import CustomerInterfaceInfo, VNetZoneChangeRequest, SDNApplyResult
from services.network_service import network_service
from services.proxmox_client import proxmox_client
from models.presets import PresetAction
from routers.base_actions import act
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/router", tags=["interface-zones"])


@router.get("/{hostname}/customer-interfaces", response_model=List[CustomerInterfaceInfo])
def get_customer_interfaces(hostname: str):
    """
    Get customer-facing interfaces for a router.

    Customer interfaces are network interfaces connected to customer networks
    (ingress points), not backbone links between routers.

    Args:
        hostname: Router hostname.

    Returns:
        List of customer interface information with zone details.
    """
    # Verify container belongs to this deployment
    container = proxmox_client.get_container_by_hostname(hostname)
    if not container:
        raise HTTPException(
            status_code=404,
            detail=f"Container {hostname} not found or not in this deployment"
        )

    try:
        interfaces = network_service.get_customer_interfaces(hostname)
        return interfaces
    except Exception as e:
        logger.error(f"Error getting customer interfaces for {hostname}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{hostname}/interface/{interface_name}/zone", response_model=CustomerInterfaceInfo)
def get_interface_zone_info(hostname: str, interface_name: str):
    """
    Get zone information for a specific customer interface.

    Args:
        hostname: Router hostname.
        interface_name: Interface name (e.g., eth2).

    Returns:
        Customer interface information with zone details.
    """
    # Verify container belongs to this deployment
    container = proxmox_client.get_container_by_hostname(hostname)
    if not container:
        raise HTTPException(
            status_code=404,
            detail=f"Container {hostname} not found or not in this deployment"
        )

    try:
        info = network_service.get_customer_interface_network_info(hostname, interface_name)
        if not info:
            raise HTTPException(
                status_code=404,
                detail=f"Interface {interface_name} not found on {hostname} or is not a customer interface"
            )
        return info
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting interface zone info: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{hostname}/interface/{interface_name}/zone", response_model=SDNApplyResult)
async def change_interface_zone(
    hostname: str,
    interface_name: str,
    request: VNetZoneChangeRequest
):
    """
    Change the zone for a customer interface's network: an interface.zone (services/base_changes.py).

    This changes the vNET zone assignment for the specified customer interface,
    potentially changing its network connectivity (e.g., from isolated to externally accessible).

    Only customer interfaces (not backbone links) can have their zones changed.

    Args:
        hostname: Router hostname.
        interface_name: Interface name.
        request: Zone change request with target zone_id.

    Returns:
        Result with success status and any warnings.
    """
    return await act(PresetAction(action="interface.zone", target=f"{hostname}:{interface_name}",
                                  params={"zone_id": request.zone_id, "vlan_tag": request.vlan_tag}))
