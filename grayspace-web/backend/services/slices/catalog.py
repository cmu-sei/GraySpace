"""Images and roles slice hosts can use.

Shipped images are images/images.csv; local images, such as a site's Windows templates, are stored
in the data directory. Roles are Ansible roles with a meta/grayspace.yml; roles without one (the
internal grayspace-host, edge-nat and edge-bgp) can't be named in a spec. Both live next to bases/
in a checkout, and Ansible copies them there on a deployed web host.
"""
import csv
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from config import settings
from models.capabilities import Image, Role
from services.slices import store

# The image of every edge router: the GraySpace Alpine image the base's routers use (packer/alpine-lxc),
# which has FRR, iptables, sshd and python3 already.
EDGE_IMAGE = "grayspace-alpine"


def root() -> Path:
    return Path(settings.bases_path).parent


def shipped_images() -> List[Image]:
    path = root() / "images" / "images.csv"
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return [
            Image(
                name=row["name"],
                type=row["type"],
                os_family=row["os_family"],
                source=row["source"],
                cores=int(row["cores"]),
                memory=int(row["memory"]),
                disk=int(row["disk"]),
                features=[x for x in (row.get("features") or "").split(";") if x],
                description=row.get("description") or "",
                entrypoint=row.get("entrypoint") or None,
                origin="shipped",
            )
            for row in csv.DictReader(f)
        ]


def _local_path() -> Path:
    return store.data_dir() / "images.json"


def local_images() -> List[Image]:
    data = store.read_json(_local_path()) or []
    return [Image.model_validate({**i, "origin": "local"}) for i in data]


def images() -> Dict[str, Image]:
    out = {i.name: i for i in shipped_images()}
    for image in local_images():
        out.setdefault(image.name, image)
    return out


def add_local_image(image: Image) -> None:
    """Raises KeyError when the name is taken."""
    with store.locked():
        if image.name in images():
            raise KeyError(image.name)
        data = store.read_json(_local_path()) or []
        data.append(image.model_dump(mode="json", exclude={"origin"}))
        store.write_json(_local_path(), data)


def delete_local_image(name: str) -> bool:
    with store.locked():
        data = store.read_json(_local_path()) or []
        kept = [i for i in data if i["name"] != name]
        if len(kept) == len(data):
            return False
        store.write_json(_local_path(), kept)
    return True


def roles() -> Dict[str, Role]:
    out = {}
    for meta in sorted((root() / "ansible" / "roles").glob("*/meta/grayspace.yml")):
        data = yaml.safe_load(meta.read_text()) or {}
        out[meta.parent.parent.name] = Role(name=meta.parent.parent.name, **data)
    return out


def image(name: str) -> Optional[Image]:
    return images().get(name)
