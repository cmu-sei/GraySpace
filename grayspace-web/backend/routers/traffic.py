"""Traffic shaping API endpoints."""
import asyncio
from fastapi import APIRouter, HTTPException
from typing import List
from models.traffic import InterfaceInfo, TrafficConfig, TrafficConfigRequest
from services.traffic_service import traffic_service
from services.proxmox_client import proxmox_client
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/traffic", tags=["traffic"])


@router.get("/{hostname}/interfaces", response_model=List[InterfaceInfo])
async def get_interfaces(hostname: str):
    """
    Get all network interfaces on a router with their current traffic shaping config.

    Args:
        hostname: Router hostname.

    Returns:
        List of interface information including current traffic config.
    """
    # Verify container belongs to this deployment
    container = await asyncio.to_thread(proxmox_client.get_container_by_hostname, hostname)
    if not container:
        raise HTTPException(
            status_code=404,
            detail=f"Container {hostname} not found or not in this deployment"
        )

    if container["status"] != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Container {hostname} is not running"
        )

    try:
        interfaces = await traffic_service.get_interfaces(hostname)
        return interfaces
    except Exception as e:
        logger.error(f"Error getting interfaces for {hostname}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{hostname}/interface/{interface_name}", response_model=InterfaceInfo)
async def get_interface(hostname: str, interface_name: str):
    """
    Get information about a specific interface.

    Args:
        hostname: Router hostname.
        interface_name: Network interface name.

    Returns:
        Interface information including current traffic config.
    """
    # Verify container belongs to this deployment
    container = await asyncio.to_thread(proxmox_client.get_container_by_hostname, hostname)
    if not container:
        raise HTTPException(
            status_code=404,
            detail=f"Container {hostname} not found or not in this deployment"
        )

    if container["status"] != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Container {hostname} is not running"
        )

    try:
        interfaces = await traffic_service.get_interfaces(hostname)
        for iface in interfaces:
            if iface.name == interface_name:
                return iface
        raise HTTPException(
            status_code=404,
            detail=f"Interface {interface_name} not found on {hostname}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting interface {interface_name} on {hostname}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{hostname}/interface/{interface_name}")
async def apply_traffic_config(
    hostname: str,
    interface_name: str,
    config: TrafficConfigRequest
):
    """
    Apply traffic shaping configuration to an interface.

    Args:
        hostname: Router hostname.
        interface_name: Network interface name.
        config: Traffic configuration to apply.

    Returns:
        Success message.
    """
    # Verify container belongs to this deployment
    container = await asyncio.to_thread(proxmox_client.get_container_by_hostname, hostname)
    if not container:
        raise HTTPException(
            status_code=404,
            detail=f"Container {hostname} not found or not in this deployment"
        )

    if container["status"] != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Container {hostname} is not running"
        )

    # Convert request to TrafficConfig
    traffic_config = TrafficConfig(
        latency_ms=config.latency_ms,
        jitter_ms=config.jitter_ms,
        bandwidth_mbps=config.bandwidth_mbps,
        packet_loss_percent=config.packet_loss_percent
    )

    try:
        success = await traffic_service.apply_traffic_config(
            hostname, interface_name, traffic_config
        )
        if not success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to apply traffic config to {hostname}:{interface_name}"
            )
        return {
            "status": "success",
            "message": f"Traffic shaping applied to {hostname}:{interface_name}",
            "config": {
                "latency_ms": config.latency_ms,
                "jitter_ms": config.jitter_ms,
                "bandwidth_mbps": config.bandwidth_mbps,
                "packet_loss_percent": config.packet_loss_percent
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying traffic config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{hostname}/interface/{interface_name}")
async def clear_traffic_config(hostname: str, interface_name: str):
    """
    Clear all traffic shaping rules from an interface.

    Args:
        hostname: Router hostname.
        interface_name: Network interface name.

    Returns:
        Success message.
    """
    # Verify container belongs to this deployment
    container = await asyncio.to_thread(proxmox_client.get_container_by_hostname, hostname)
    if not container:
        raise HTTPException(
            status_code=404,
            detail=f"Container {hostname} not found or not in this deployment"
        )

    if container["status"] != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Container {hostname} is not running"
        )

    try:
        success = await traffic_service.clear_traffic_config(hostname, interface_name)
        if not success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to clear traffic config from {hostname}:{interface_name}"
            )
        return {
            "status": "success",
            "message": f"Traffic shaping cleared from {hostname}:{interface_name}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error clearing traffic config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
