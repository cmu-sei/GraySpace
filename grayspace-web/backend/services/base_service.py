"""The deployed base version and its health."""
import asyncio
import logging
from typing import Any, Dict, List

from models.base import BaseVersionRef, BgpHealth, BgpPeer, DeployedBase, RouterHealth
from services.prometheus_client import prometheus_client
from services.proxmox_client import proxmox_client
from services.topology_service import topology_service

logger = logging.getLogger(__name__)


def version_ref(tfvars: Dict[str, Any]):
    """The deployed base version recorded in the tfvars, or None."""
    base = tfvars.get("base")
    return BaseVersionRef(**base) if base else None


async def _router_health(expected: List[str]) -> RouterHealth:
    try:
        containers = await asyncio.to_thread(proxmox_client.get_deployment_containers)
    except Exception as e:
        logger.warning(f"Proxmox unavailable for base health: {e}")
        containers = []
    # The client returns [] when listing fails, which can't be told apart from nothing deployed.
    if not containers:
        return RouterHealth(expected=len(expected), running=None)
    running = {c["name"] for c in containers if c.get("status") == "running"}
    not_running = [h for h in expected if h not in running]
    return RouterHealth(expected=len(expected), running=len(expected) - len(not_running), not_running=not_running)


async def _bgp_health() -> BgpHealth:
    peers = await prometheus_client.get_bgp_peer_states()
    if not peers:
        return BgpHealth(total=None, established=None)
    down = [BgpPeer(router=p["router"], peer_address=p["peer_address"], state=p["state"]) for p in peers if p["state"] != "up"]
    return BgpHealth(total=len(peers), established=len(peers) - len(down), not_established=down)


async def get_deployed_base() -> DeployedBase:
    tfvars = topology_service.tfvars_data
    expected = list(tfvars.get("routers", {}))
    routers, bgp = await asyncio.gather(_router_health(expected), _bgp_health())

    if not tfvars:
        status = "not_deployed"
    elif not expected or routers.running is None or bgp.total is None:
        status = "unknown"
    elif not routers.not_running and not bgp.not_established:
        status = "healthy"
    else:
        status = "degraded"
    return DeployedBase(version=version_ref(tfvars), status=status, routers=routers, bgp=bgp)
