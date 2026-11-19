"""DNS API endpoints for querying zone data."""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from services.dns_service import dns_service
from models.network import (
    AddDNSRecordRequest,
    AddDNSRecordResult,
    CreateDomainRequest,
    CreateDomainResult,
)
from models.presets import PresetAction
from routers.base_actions import act
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dns", tags=["dns"])

# Static routes must come before parameterized /{hostname}/ routes
@router.post("/domains", response_model=CreateDomainResult)
async def create_domain(request: CreateDomainRequest):
    """Create a new domain with TLD delegation and hosting zone file (a dns.domain.create, services/base_changes.py)."""
    return await act(PresetAction(action="dns.domain.create", params=request.model_dump()))


@router.get("/{hostname}/zones", response_model=Dict[str, Any])
async def get_dns_zones(hostname: str):
    """
    Get DNS zone data for a specific DNS server.

    SSHes into the container and reads zone files based on the server's role.
    Returns empty zones list if the server is not yet configured.

    Args:
        hostname: DNS server hostname.

    Returns:
        Dict with hostname, role, and parsed zone records.
    """
    try:
        return await dns_service.get_zones(hostname)
    except Exception as e:
        logger.error(f"Error retrieving DNS zones for {hostname}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{hostname}/records", response_model=AddDNSRecordResult)
async def add_dns_record(hostname: str, request: AddDNSRecordRequest):
    """Add a DNS record to a zone on a specific DNS server (a dns.record.set, services/base_changes.py)."""
    return await act(PresetAction(action="dns.record.set", params={"server": hostname, **request.model_dump()}))
