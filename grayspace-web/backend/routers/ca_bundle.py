"""The base CAs' root certificates."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from services.auth import require_token
from services.ca_service import ca_service

router = APIRouter(prefix="/api/v1/ca", tags=["ca"], dependencies=[Depends(require_token)])


@router.get("/bundle", response_class=PlainTextResponse,
            responses={200: {"content": {"application/x-pem-file": {}}, "description": "Concatenated PEM root certificates"}})
async def get_ca_bundle():
    """The root certificates of every deployed base CA, for hosts outside GraySpace (such as GHOSTS) that must trust them."""
    roots = await ca_service.get_ca_bundle()
    pems = [pem for pem in roots.values() if pem]
    if not pems:
        raise HTTPException(status_code=503, detail="No CA root certificate could be read")
    missing = [ca for ca, pem in roots.items() if not pem]
    headers = {"X-GraySpace-Missing-CAs": ",".join(missing)} if missing else {}
    return PlainTextResponse("".join(pems), media_type="application/x-pem-file", headers=headers)
