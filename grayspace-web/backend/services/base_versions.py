"""Shipped base versions under bases/<name>/<version>/."""
import importlib.util
import logging
from pathlib import Path
from typing import List, Optional

from config import settings
from models.base import (
    BaseVersion,
    BaseVersionDetail,
    SnapshotIsp,
    SnapshotLink,
    SnapshotLocation,
    SnapshotRouter,
    VersionSummary,
)
from services.topology_service import topology_service

logger = logging.getLogger(__name__)

_topology = None

# Versions no longer shipped, and what replaces them. 2026.10 had base clients (contract 1.18)
RETIRED = {"grayspace/2026.10": "grayspace/2026.11"}


def topology():
    """scripts/topology.py, the same loader task parse uses, loaded from settings.scripts_path."""
    global _topology
    if _topology is None:
        path = Path(settings.scripts_path) / "topology.py"
        spec = importlib.util.spec_from_file_location("topology", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _topology = module
    return _topology


def _version_dirs():
    """{name: directory} for every shipped version, e.g. {"grayspace/2026.11": Path(...)}."""
    root = Path(settings.bases_path)
    return {f"{d.parent.name}/{d.name}": d for d in sorted(root.glob("*/*")) if d.is_dir()}


def _deployed_sha():
    base = topology_service.tfvars_data.get("base") or {}
    return base.get("csv_sha256")


def _split(values):
    """["ABR,RR"] -> ["ABR", "RR"]: the CSVs' array quirk stays out of the API."""
    return [part for value in values or [] for part in value.split(",") if part]


def _load(name, directory, detail):
    cls = BaseVersionDetail if detail else BaseVersion
    t = topology()
    try:
        topo = t.load(directory)
    except t.LoadError as e:
        return cls(name=name, csv_sha256=None, deployed=False, summary=None, problems=e.problems)

    sha = t.csv_sha256(directory)
    links = [r for r in topo.rels if r.source == "connections.csv"]
    summary = VersionSummary(
        isps=len(topo.of("ISP")),
        locations=len(topo.of("Location")),
        routers=len(topo.of("Router")),
        links=len(links),
        core_services=len(topo.of("CoreService")),
    )
    version = dict(name=name, csv_sha256=sha, deployed=sha == _deployed_sha(), summary=summary)
    if not detail:
        return BaseVersion(**version)

    def first_end(router, rel_type):
        rels = topo.out(router.id, rel_type)
        return rels[0].end if rels else None

    return BaseVersionDetail(
        **version,
        isps=[SnapshotIsp(name=i.id, asn=i.props.get("asn"), public_block=i.props.get("public_block")) for i in topo.of("ISP")],
        locations=[
            SnapshotLocation(name=loc.id, latitude=loc.props.get("latitude"), longitude=loc.props.get("longitude"))
            for loc in topo.of("Location")
        ],
        routers=[
            SnapshotRouter(
                hostname=r.id,
                isp=first_end(r, "OPERATED_BY"),
                location=first_end(r, "LOCATED_IN"),
                roles=_split(r.props.get("roles")),
                ospf_areas=_split(r.props.get("ospf_areas")),
                loopback_ip=r.props.get("loopback_ip"),
                customer_net=r.props.get("customer_net"),
            )
            for r in topo.of("Router")
        ],
        links=[
            SnapshotLink(start=r.start, end=r.end, type=r.type, subnet=r.props.get("subnet"), ospf_area=r.props.get("ospf_area"))
            for r in links
        ],
    )


def list_versions() -> List[BaseVersion]:
    return [_load(name, directory, detail=False) for name, directory in _version_dirs().items()]


def get_version(name: str) -> Optional[BaseVersionDetail]:
    """A version and its snapshot, or None. Only names of shipped versions are served, so the
    name can't be used to read other paths."""
    directory = _version_dirs().get(name)
    return _load(name, directory, detail=True) if directory else None


def resolve(name: Optional[str] = None):
    """(name, directory) for a shipped version, or for the deployed one when name is None.
    None when there is no such version, or the deployed CSVs match no shipped version."""
    dirs = _version_dirs()
    if name is not None:
        return (name, dirs[name]) if name in dirs else None
    sha = _deployed_sha()
    for candidate, directory in dirs.items():
        if sha and topology().csv_sha256(directory) == sha:
            return candidate, directory
    return None


def not_shipped(name: str) -> str:
    """Why a version name can't be used: there's no such version, or it was retired."""
    successor = RETIRED.get(name)
    if successor:
        return f"Base version '{name}' was retired: hosts at the edge are enclaves now; {successor} replaces it"
    return f"No shipped base version '{name}'"
