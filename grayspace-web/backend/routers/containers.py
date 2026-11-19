"""Container management API endpoints."""
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from services.proxmox_client import proxmox_client
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/containers", tags=["containers"])


@router.get("", response_model=List[Dict[str, Any]])
def get_containers():
    """
    Get all containers for this deployment (filtered by environment_id).

    Returns:
        List of containers with status information.
    """
    try:
        containers = proxmox_client.get_deployment_containers()
        return containers
    except Exception as e:
        logger.error(f"Error retrieving containers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{hostname}", response_model=Dict[str, Any])
def get_container(hostname: str):
    """
    Get information about a specific container.

    Args:
        hostname: Container hostname.

    Returns:
        Container information.
    """
    container = proxmox_client.get_container_by_hostname(hostname)
    if not container:
        raise HTTPException(
            status_code=404,
            detail=f"Container {hostname} not found or not in this deployment"
        )
    return container


@router.post("/{hostname}/start")
def start_container(hostname: str):
    """
    Start a container.

    Args:
        hostname: Container hostname.

    Returns:
        Success message.
    """
    success = proxmox_client.start_container(hostname)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to start container {hostname}"
        )
    return {"status": "success", "message": f"Container {hostname} started"}


@router.post("/{hostname}/stop")
def stop_container(hostname: str):
    """
    Stop a container.

    Args:
        hostname: Container hostname.

    Returns:
        Success message.
    """
    success = proxmox_client.stop_container(hostname)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to stop container {hostname}"
        )
    return {"status": "success", "message": f"Container {hostname} stopped"}


@router.post("/{hostname}/restart")
def restart_container(hostname: str):
    """
    Restart a container.

    Args:
        hostname: Container hostname.

    Returns:
        Success message.
    """
    success = proxmox_client.restart_container(hostname)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to restart container {hostname}"
        )
    return {"status": "success", "message": f"Container {hostname} restarted"}
