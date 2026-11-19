"""Link traffic shaping API endpoints (symmetrical application to both ends)."""
from fastapi import APIRouter, HTTPException
from models.presets import PresetAction
from models.traffic import LinkTrafficConfig, TrafficConfigRequest
from routers.base_actions import act
from services.traffic_service import traffic_service
from services.topology_service import topology_service
from services.prometheus_client import prometheus_client
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/links", tags=["link-traffic"])


@router.get("/{link_id}/traffic", response_model=LinkTrafficConfig)
async def get_link_traffic_config(link_id: str):
    """
    Get current traffic shaping configuration for a link.

    Args:
        link_id: Link identifier (format: "router1:router2").

    Returns:
        LinkTrafficConfig with current settings from both ends.
    """
    # Verify link exists
    conn_info = topology_service.get_connection_interfaces(link_id)
    if not conn_info:
        raise HTTPException(
            status_code=404,
            detail=f"Link {link_id} not found"
        )

    try:
        config = await traffic_service.get_link_traffic_config(link_id)
        if not config:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to get traffic config for link {link_id}"
            )
        return config
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting link traffic config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{link_id}/rates")
async def get_link_traffic_rates(link_id: str):
    """
    Get live traffic rates for both endpoints of a link.

    Args:
        link_id: Link identifier (format: "router1:router2").

    Returns:
        Dictionary with source and target endpoint rate data.
    """
    conn_info = topology_service.get_connection_interfaces(link_id)
    if not conn_info:
        raise HTTPException(
            status_code=404,
            detail=f"Link {link_id} not found"
        )

    try:
        rates = await prometheus_client.get_link_traffic_rates(
            conn_info["source_hostname"],
            conn_info["source_interface"],
            conn_info["target_hostname"],
            conn_info["target_interface"],
        )
        return rates
    except Exception as e:
        logger.error(f"Error getting link traffic rates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{link_id}/traffic")
async def apply_link_traffic_config(link_id: str, config: TrafficConfigRequest):
    """
    Apply traffic shaping configuration to both ends of a link (symmetrically): a link.condition
    (services/base_changes.py). A link an instance holds is refused (409).

    Args:
        link_id: Link identifier (format: "router1:router2").
        config: Traffic configuration to apply.

    Returns:
        Success message.
    """
    return await act(PresetAction(action="link.condition", target=link_id, params=config.model_dump()))


@router.delete("/{link_id}/traffic")
async def clear_link_traffic_config(link_id: str):
    """
    Clear traffic shaping rules from both ends of a link: a link.clear (services/base_changes.py).

    Args:
        link_id: Link identifier (format: "router1:router2").

    Returns:
        Success message.
    """
    return await act(PresetAction(action="link.clear", target=link_id))
