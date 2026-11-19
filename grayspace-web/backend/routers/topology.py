"""Topology API endpoints."""
import asyncio
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from services.topology_service import topology_service
from services.prometheus_client import prometheus_client
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/topology", tags=["topology"])


@router.get("/routers", response_model=List[Dict[str, Any]])
def get_routers():
    """
    Get all deployed routers with their location data.

    Only returns routers actually deployed in Proxmox (filtered by environment_id).

    Returns:
        List of routers with hostname, IPs, ISP, location (lat/long), roles, OSPF areas.
    """
    try:
        routers = topology_service.get_routers_with_locations()
        return routers
    except Exception as e:
        logger.error(f"Error retrieving routers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/links", response_model=List[Dict[str, Any]])
def get_links():
    """
    Get all router-to-router connections for deployed routers.

    Only returns connections where both routers are deployed in Proxmox.

    Returns:
        List of connections with source/target routers, IPs, subnets, and types.
    """
    try:
        connections = topology_service.get_connections()
        return connections
    except Exception as e:
        logger.error(f"Error retrieving links: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/isps", response_model=List[Dict[str, Any]])
def get_isps():
    """
    Get all ISPs from deployed routers.

    Only returns ISPs that have at least one router deployed in Proxmox.

    Returns:
        List of ISPs.
    """
    try:
        isps = topology_service.get_isps()
        return isps
    except Exception as e:
        logger.error(f"Error retrieving ISPs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/router/{hostname}", response_model=Dict[str, Any])
def get_router_detail(hostname: str):
    """
    Get detailed information about a specific router (if deployed).

    Args:
        hostname: Router hostname (e.g., "lum-sfr").

    Returns:
        Detailed router information including neighbors and configuration.
    """
    try:
        router_detail = topology_service.get_router_detail(hostname)
        if not router_detail:
            raise HTTPException(status_code=404, detail=f"Router {hostname} not found or not deployed")
        return router_detail
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving router detail: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dns-servers", response_model=List[Dict[str, Any]])
def get_dns_servers():
    """Get all DNS servers with deployment status and hosting router info."""
    try:
        return topology_service.get_dns_servers()
    except Exception as e:
        logger.error(f"Error retrieving DNS servers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/router/{hostname}/attached", response_model=List[Dict[str, Any]])
def get_router_attached(hostname: str):
    """The core DNS servers and CAs on a router."""
    try:
        return topology_service.get_router_attached(hostname)
    except Exception as e:
        logger.error(f"Error retrieving what is attached to router {hostname}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/router/{hostname}/state")
async def get_router_state(hostname: str):
    """
    Get live BGP, OSPF, and interface state for a specific router.

    Args:
        hostname: Router hostname.

    Returns:
        Combined state with bgp_peers, ospf_neighbors, and interfaces.
    """
    try:
        deployed = await asyncio.to_thread(topology_service.get_deployed_hostnames)
        if hostname not in deployed:
            raise HTTPException(status_code=404, detail=f"Router {hostname} not found or not deployed")

        state = await prometheus_client.get_router_state(hostname)

        # Map peer/neighbor IPs to hostnames
        ip_map = topology_service.get_ip_to_hostname_map()
        for peer in state["bgp_peers"]:
            peer["peer_hostname"] = ip_map.get(peer["peer_address"], peer["peer_address"])
        for neighbor in state["ospf_neighbors"]:
            neighbor["neighbor_hostname"] = ip_map.get(neighbor["neighbor_address"], neighbor["neighbor_address"])

        return state
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving router state: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/router/{hostname}/interface-stats")
async def get_router_interface_stats(hostname: str):
    """
    Get per-interface traffic stats for a specific router.

    Args:
        hostname: Router hostname.

    Returns:
        Dictionary with hostname and list of interface stat objects.
    """
    try:
        deployed = await asyncio.to_thread(topology_service.get_deployed_hostnames)
        if hostname not in deployed:
            raise HTTPException(status_code=404, detail=f"Router {hostname} not found or not deployed")

        interfaces = await prometheus_client.get_interface_stats(hostname)
        return {"hostname": hostname, "interfaces": interfaces}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving interface stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reload")
async def reload_topology():
    """
    Reload topology data from Proxmox and tfvars.

    This should be called after running terraform apply to refresh the deployment state.

    Returns:
        Success message.
    """
    try:
        topology_service.reload()

        # Also reload state monitor IP mapping and link topology
        from tasks.state_monitor import state_monitor
        await state_monitor._load_ip_mapping()
        state_monitor._load_link_topology()

        return {"status": "success", "message": "Topology data reloaded"}
    except Exception as e:
        logger.error(f"Error reloading topology: {e}")
        raise HTTPException(status_code=500, detail=str(e))
