"""CA API endpoints for certificate authority management."""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from services.ca_service import ca_service
from services.topology_service import topology_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ca", tags=["ca"])


class IssueCertRequest(BaseModel):
    domain: str = Field(..., description="Domain to issue certificate for")
    ca_hostname: Optional[str] = Field(default=None, description="CA hostname (random if omitted)")


class IssueCertResult(BaseModel):
    success: bool
    message: str
    certificate: Optional[str] = None
    private_key: Optional[str] = None
    ca_hostname: Optional[str] = None


class RevokeCertRequest(BaseModel):
    serial: str = Field(..., description="Certificate serial number to revoke")
    reason: Optional[str] = Field(default=None, description="Revocation reason")


class RevokeCertResult(BaseModel):
    success: bool
    message: str


@router.get("/servers")
def get_ca_servers() -> List[Dict[str, Any]]:
    """List all CA servers with their deployment status."""
    return topology_service.get_ca_servers()


@router.get("/{hostname}/certificates")
async def get_certificates(hostname: str) -> Dict[str, Any]:
    """Get certificates issued by a specific CA."""
    ca_servers = {s["hostname"]: s for s in topology_service.get_ca_servers()}
    if hostname not in ca_servers:
        raise HTTPException(status_code=404, detail=f"CA server '{hostname}' not found")
    if not ca_servers[hostname]["deployed"]:
        raise HTTPException(status_code=400, detail=f"CA server '{hostname}' is not deployed")

    try:
        certs = await ca_service.get_certificates(hostname)
        return {"hostname": hostname, "certificates": certs}
    except Exception as e:
        logger.error(f"Error fetching certificates from '{hostname}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/certificates/issue", response_model=IssueCertResult)
async def issue_certificate(request: IssueCertRequest):
    """Issue a new certificate from a CA."""
    domain = request.domain.strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Domain is required")

    try:
        result = await ca_service.issue_certificate(domain, request.ca_hostname)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])
        # A tracked change: Reset changes revokes it
        from services.mutation_tracker import mutation_tracker
        from services.slices.stages import _serial
        mutation_tracker.add_certificate_issued(result["ca_hostname"], domain, _serial(result.get("certificate") or ""))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error issuing certificate for '{domain}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{hostname}/certificates/revoke", response_model=RevokeCertResult)
async def revoke_certificate(hostname: str, request: RevokeCertRequest):
    """Revoke a certificate on a specific CA."""
    ca_servers = {s["hostname"]: s for s in topology_service.get_ca_servers()}
    if hostname not in ca_servers:
        raise HTTPException(status_code=404, detail=f"CA server '{hostname}' not found")
    if not ca_servers[hostname]["deployed"]:
        raise HTTPException(status_code=400, detail=f"CA server '{hostname}' is not deployed")

    try:
        result = await ca_service.revoke_certificate(hostname, request.serial, request.reason)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])
        from services.mutation_tracker import mutation_tracker
        mutation_tracker.add_certificate_revoked(hostname, request.serial)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error revoking certificate on '{hostname}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
