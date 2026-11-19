"""The image catalog and roles slice hosts can use."""
from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from models.capabilities import Image, Role
from services.auth import require_token
from services.slices import catalog, store

router = APIRouter(prefix="/api/v1", tags=["images"], dependencies=[Depends(require_token)])


@router.get("/images", response_model=List[Image])
async def list_images():
    """Shipped images (images/images.csv) and this site's local images."""
    return sorted(catalog.images().values(), key=lambda i: i.name)


@router.post("/images", response_model=Image, status_code=201)
async def add_image(image: Image = Body(...)):
    """
    Register a local image: a download URL, an OCI reference, `template:<vmid>` for a Proxmox VM
    template (such as a Windows template built with packer/windows/), or `volume:<storage>:<path>`
    for a template already on the node.
    """
    if image.source.startswith("template:") and image.type != "vm":
        raise HTTPException(status_code=422, detail="template: sources are VM templates; use type vm")
    if image.source.startswith("template:") and not image.source.split(":", 1)[1].isdigit():
        raise HTTPException(status_code=422, detail="template:<vmid> needs a numeric VM ID")
    image = image.model_copy(update={"origin": "local"})
    try:
        catalog.add_local_image(image)
    except KeyError:
        raise HTTPException(status_code=409, detail=f"An image '{image.name}' exists already")
    return image


@router.delete("/images/{name}", status_code=204, response_class=Response)
async def delete_image(name: str):
    """Remove a local image. Shipped images can't be removed; images live instances use are refused."""
    image = catalog.image(name)
    if image is None:
        raise HTTPException(status_code=404, detail=f"No image '{name}'")
    if image.origin != "local":
        raise HTTPException(status_code=409, detail=f"'{name}' is a shipped image")
    users = [i.id for i in store.live_instances() if i.allocation and name in i.allocation.images]
    if users:
        raise HTTPException(status_code=409, detail=f"Live instances use '{name}': {', '.join(users)}")
    catalog.delete_local_image(name)
    return Response(status_code=204)


@router.get("/roles", response_model=List[Role])
async def list_roles():
    """Roles slice hosts can take, with the OS families they support and the role_vars they read."""
    return list(catalog.roles().values())
