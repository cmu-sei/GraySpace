"""Render an allocation into what Terraform and Ansible read.

- terraform/slice/instances/<id>.tfvars.json: fully resolved (bridges, addresses, gateways,
  resolvers, image volumes), so the slice module has no allocation logic.
- terraform/images/images.tfvars.json: one download per image any live instance uses.
- ansible/slice-inventory/<id>.ini and <id>.vars.json: the per-instance inventory, outside
  ansible/inventory/ so base runs never see slice hosts.

Everything here is a pure function of its inputs, so the output can be golden-tested.
"""
import base64
import ipaddress
import json
import re

import yaml
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from models.capabilities import Image
from models.instances import Allocation, HostAllocation, SiteAllocation
from services.slices.catalog import EDGE_IMAGE
from services.slices.plan import EDGE_SIZE

LXC_OS_TYPES = {"ubuntu": "ubuntu", "debian": "debian", "alpine": "alpine"}
TLS_CERT = "/tmp/grayspace-tls.crt"
TLS_KEY = "/tmp/grayspace-tls.key"


@dataclass
class RenderEnv:
    environment_id: str
    filestore: str
    # How the base got the GraySpace Alpine image, whose copy edges use (terraform/routers.tf):
    # TF_VAR_alpine_image_url instead of its catalog URL, and TF_VAR_alpine_image_transfer, which
    # the images module follows for GraySpace's other images too (uploaded)
    alpine_image_url: Optional[str] = None
    alpine_image_transfer: str = "upload"

    @classmethod
    def from_env(cls, env: Dict[str, str]) -> "RenderEnv":
        missing = [k for k in ("TF_VAR_environment_id", "TF_VAR_proxmox_filestore") if not env.get(k)]
        if missing:
            raise ValueError(f"The environment file doesn't set {', '.join(missing)}")
        return cls(
            environment_id=env["TF_VAR_environment_id"],
            filestore=env["TF_VAR_proxmox_filestore"],
            alpine_image_url=env.get("TF_VAR_alpine_image_url") or None,
            alpine_image_transfer=env.get("TF_VAR_alpine_image_transfer") or "upload",
        )


def image_volume(env: RenderEnv, image: Image) -> dict:
    """How an image is provided: downloaded (and its volume ID), or already a Proxmox template.

    Download volume IDs are <datastore>:<content type>/<file name>, the IDs proxmox_download_file,
    proxmox_virtual_environment_file and proxmox_oci_image report, so the slice module can name them
    without reading the images state. Uploads are named apart from downloads, as the base names its
    Alpine image, so switching modes creates one file before deleting the other.
    """
    if image.source.startswith("template:"):
        return {"kind": "template", "clone_vm_id": int(image.source.split(":", 1)[1])}
    if image.source.startswith("volume:"):
        return {"kind": "volume", "volume_id": image.source.split(":", 1)[1]}
    upload = uploaded(env, image)
    name = f"{env.environment_id}-img-{'up-' if upload else ''}{image.name}"
    kind = "upload" if upload else "download"
    if image.type == "oci":
        file_name = f"{name}.tar"
        return {"kind": "oci", "file_name": file_name, "volume_id": f"{env.filestore}:vztmpl/{file_name}"}
    if image.type == "vm":
        file_name = f"{name}.qcow2"
        return {"kind": kind, "content_type": "import", "file_name": file_name, "volume_id": f"{env.filestore}:import/{file_name}"}
    match = re.search(r"(\.tar\.(?:zst|xz|gz))$", image.source)
    file_name = name + (match.group(1) if match else ".tar.gz")
    return {"kind": kind, "content_type": "vztmpl", "file_name": file_name, "volume_id": f"{env.filestore}:vztmpl/{file_name}"}


def uploaded(env: RenderEnv, image: Image) -> bool:
    """GraySpace's own images (packer/, grayspace-* in the catalog) follow the base's Alpine image
    transfer: until they are public release assets, the node may not reach where they are served."""
    return (image.origin == "shipped" and image.name.startswith("grayspace-") and image.type != "oci"
            and env.alpine_image_transfer == "upload")


def base_alpine_volume(env: RenderEnv, image: Image) -> str:
    """The base's copy of the GraySpace Alpine image, named as terraform/routers.tf names it.

    Edges use it rather than a download of their own: the node may not reach the image's URL (its
    downloads go through the cluster's proxy), and the base already has it."""
    url = env.alpine_image_url or image.source
    prefix = "base-up" if env.alpine_image_transfer == "upload" else "base"
    return f"{env.filestore}:vztmpl/{env.environment_id}-{prefix}-{url.rstrip('/').rsplit('/', 1)[-1]}"


def images_tfvars(env: RenderEnv, images: Iterable[Image]) -> dict:
    """The images module's input: every image in use that needs a download or an upload."""
    downloads, uploads, oci = {}, {}, {}
    for image in sorted(images, key=lambda i: i.name):
        vol = image_volume(env, image)
        if vol["kind"] == "download":
            downloads[image.name] = {"url": image.source, "content_type": vol["content_type"], "file_name": vol["file_name"]}
        elif vol["kind"] == "upload":
            uploads[image.name] = {"url": image.source, "content_type": vol["content_type"], "file_name": vol["file_name"]}
        elif vol["kind"] == "oci":
            oci[image.name] = {"reference": image.source, "file_name": vol["file_name"]}
    return {"downloads": downloads, "uploads": uploads, "oci_images": oci}


def _cidr(ip: str, network: str) -> str:
    return f"{ip}/{ipaddress.IPv4Network(network).prefixlen}"


def _edge_interfaces(site: SiteAllocation) -> List[dict]:
    """WANs first, then segments. A NAT site's WAN carries the default route via the base router;
    a routed site's edge learns its routes over BGP, so no WAN has a static gateway."""
    out = []
    for i, wan in enumerate(site.wans):
        out.append({"bridge": wan.bridge, "address": _cidr(wan.ip, wan.customer_net), "gateway": None if site.routed or i else wan.gateway})
    for seg in site.segments:
        out.append({"bridge": seg.vnet, "address": _cidr(seg.gateway, seg.cidr), "gateway": None})
    return out


def _init_script(entrypoint: str) -> str:
    """The OCI entrypoint that writes the certificate passed in env, then runs the image's command."""
    script = (
        "umask 077\n"
        f'echo "$GRAYSPACE_TLS_CERT" | base64 -d > {TLS_CERT}\n'
        f'echo "$GRAYSPACE_TLS_KEY" | base64 -d > {TLS_KEY}\n'
        f"{entrypoint}\n"
    )
    return base64.b64encode(script.encode()).decode()


def _oci_env(host: HostAllocation, image: Image, cert: Optional[tuple]) -> tuple:
    """(env, entrypoint) for an OCI host. tls-kestrel images (Pandora) serve HTTP on 80, and HTTPS
    on 443 with the slice certificate when the host has one; the host's own env wins."""
    env = {}
    entrypoint = None
    if "tls-kestrel" in image.features:
        env["ASPNETCORE_URLS"] = "http://+:80"
        if cert and image.entrypoint:
            env.update({
                "ASPNETCORE_URLS": "http://+:80;https://+:443",
                "ASPNETCORE_Kestrel__Certificates__Default__Path": TLS_CERT,
                "ASPNETCORE_Kestrel__Certificates__Default__KeyPath": TLS_KEY,
                "GRAYSPACE_TLS_CERT": base64.b64encode(cert[0].encode()).decode(),
                "GRAYSPACE_TLS_KEY": base64.b64encode(cert[1].encode()).decode(),
                "GRAYSPACE_INIT": _init_script(image.entrypoint),
            })
            entrypoint = "/bin/sh -c 'eval \"$(echo \"$GRAYSPACE_INIT\" | base64 -d)\"'"
    env.update(host.env)
    return env, entrypoint


def instance_tfvars(alloc: Allocation, images: Dict[str, Image], env: RenderEnv, certs: Optional[Dict[str, tuple]] = None,
                    crucible_team_id: Optional[str] = None) -> dict:
    """The slice module's input for one instance. certs: {hostname: (cert PEM, key PEM)}, for OCI
    hosts, which take their certificate at creation."""
    certs = certs or {}
    volumes = {name: image_volume(env, images[name]) for name in alloc.images if name in images}
    out = {
        "instance": alloc.prefix,
        "vnets": dict(sorted(alloc.vnets.items())),
        "edge_template": "",
        "edges": {},
        "lxc_hosts": {},
        "oci_hosts": {},
        "vm_hosts": {},
    }
    if alloc.sites:
        out["edge_template"] = base_alpine_volume(env, images[EDGE_IMAGE])
    for site in alloc.sites:
        out["edges"][site.edge] = {**EDGE_SIZE, "resolver": site.resolver, "interfaces": _edge_interfaces(site)}
    for h in alloc.hosts:
        common = {
            "cores": h.cores, "memory": h.memory, "disk": h.disk, "resolver": h.resolver,
            "bridge": h.bridge, "address": f"{h.ip}/{h.prefixlen}", "gateway": h.gateway,
        }
        vol = volumes[h.image]
        if h.type == "lxc":
            out["lxc_hosts"][h.hostname] = {**common, "template": vol["volume_id"], "os_type": LXC_OS_TYPES.get(h.os_family, "unmanaged")}
        elif h.type == "oci":
            host_env, entrypoint = _oci_env(h, images[h.image], certs.get(h.hostname))
            out["oci_hosts"][h.hostname] = {**common, "template": vol["volume_id"], "env": host_env, "entrypoint": entrypoint}
        else:
            out["vm_hosts"][h.hostname] = {
                **common, "os_family": h.os_family,
                "import_from": vol.get("volume_id") if vol["kind"] != "template" else None,
                "clone_vm_id": vol.get("clone_vm_id"),
            }
    # The guests the spec marks for participants (crucible: true), registered in the base's Crucible
    # Player view team when it has one (terraform/slice/crucible.tf). Left out when there are none.
    marked = [s.edge for s in alloc.sites if s.crucible] + [h.hostname for h in alloc.hosts if h.crucible]
    if marked:
        out["crucible_guests"] = marked
    if crucible_team_id:
        out["crucible_team_id"] = crucible_team_id
    return out


# --- Ansible ---


def _ansible_connection(h: Optional[HostAllocation]) -> str:
    if h is None or h.type == "lxc":
        return "ansible_user=root"
    if h.os_family == "windows":
        return "ansible_user=Administrator ansible_shell_type=powershell ansible_shell_executable=None"
    return "ansible_user=grayspace ansible_become=true"


def inventory(alloc: Allocation, mgmt_ips: Dict[str, str], key_file: str) -> str:
    """ansible/slice-inventory/<id>.ini: edges, managed hosts, and a group per OS family."""
    edges = [s.edge for s in alloc.sites]
    managed = [h for h in alloc.hosts if h.managed]
    lines = [f"# Slice instance {alloc.prefix}, written by GraySpace. Used only by ansible/slice.yml.", "", "[edges]"]
    lines += [f"{e} ansible_host={mgmt_ips[e]} {_ansible_connection(None)}" for e in edges if e in mgmt_ips]
    lines += ["", "[hosts]"]
    lines += [f"{h.hostname} ansible_host={mgmt_ips[h.hostname]} {_ansible_connection(h)}" for h in managed if h.hostname in mgmt_ips]
    for family in sorted({h.os_family for h in managed}):
        lines += ["", f"[{family}]"] + [h.hostname for h in managed if h.os_family == family and h.hostname in mgmt_ips]
    lines += ["", "[alpine:children]", "edges"] if edges else []
    lines += ["", "[all:vars]", f"ansible_ssh_private_key_file={key_file}", "ansible_ssh_common_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'", ""]
    return "\n".join(lines)


def _mgmt_routes(role_vars: dict) -> List[str]:
    """Addresses that must stay on the management network: the GHOSTS API."""
    host = (role_vars.get("ghosts-client") or {}).get("api_host")
    return [host] if host else []


def ansible_vars(alloc: Allocation, spec_role_vars: dict, domains: List[str], ca_bundle: Optional[str],
                 certificates: Dict[str, dict], neighbors: Optional[Dict[str, list]] = None) -> dict:
    """ansible/slice-inventory/<id>.vars.json, passed with -e @: everything roles need per host.

    certificates: {hostname: {cert, key}} file paths. neighbors: {edge: [{ip, asn, router}]} for
    routed sites' BGP sessions with base routers.
    """
    neighbors = neighbors or {}
    edges = {}
    for site in alloc.sites:
        wans = [{"interface": f"eth{i}", "ip": w.ip, "prefixlen": ipaddress.IPv4Network(w.customer_net).prefixlen, "gateway": w.gateway, "router": w.router}
                for i, w in enumerate(site.wans)]
        segments = [{"interface": f"eth{len(site.wans) + j}", "cidr": s.cidr, "gateway": s.gateway, "name": s.name}
                    for j, s in enumerate(site.segments)]
        published = [{"public_ip": h.public_ip, "private_ip": h.ip, "hostname": h.hostname}
                     for h in alloc.hosts if h.site == site.name and h.public_ip and not site.routed]
        edges[site.edge] = {
            "site": site.name,
            "routed": site.routed,
            "wans": wans,
            "segments": segments,
            "published": published,
            "resolver": site.resolver,
            "mgmt_interface": f"eth{len(site.wans) + len(site.segments)}",
            "bgp": {"asn": site.asn, "prefix": site.public_prefix, "neighbors": neighbors.get(site.edge, [])} if site.routed else None,
        }
    hosts = {}
    for h in alloc.hosts:
        if not h.managed:
            continue
        hosts[h.hostname] = {
            "name": h.name,
            "site": h.site,
            "segment": h.segment,
            "ip": h.ip,
            "prefixlen": h.prefixlen,
            "gateway": h.gateway,
            "resolver": h.resolver,
            "public_ip": h.public_ip,
            "os_family": h.os_family,
            "roles": h.roles,
            "labels": h.labels,
            "sim_interface": "eth0",
            "mgmt_interface": "eth1",
            "certificate": certificates.get(h.hostname),
        }
    return {
        "slice_instance": alloc.prefix,
        "slice_domains": domains,
        "slice_ca_bundle": ca_bundle,
        "slice_mgmt_routes": _mgmt_routes(spec_role_vars),
        "slice_role_vars": spec_role_vars,
        "slice_edges": edges,
        "slice_hosts": hosts,
        "slice_site_hosts": {
            site.name: [{"name": h.name, "hostname": h.hostname, "ip": h.ip} for h in alloc.hosts if h.site == site.name]
            for site in alloc.sites
        },
    }


def dumps(data) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


class _UnsafeDumper(yaml.SafeDumper):
    pass


def _unsafe_str(dumper, value):
    return dumper.represent_scalar("!unsafe", value)


_UnsafeDumper.add_representer(str, _unsafe_str)


def ansible_vars_yaml(variables: dict) -> str:
    """The vars file for -e @: YAML with every string tagged !unsafe, so Ansible never templates
    them. role_vars, labels and names come from the spec; a value like {{ lookup('pipe', …) }}
    must stay text (GHOSTS timelines contain {{…}} handler templates too)."""
    header = "# Written by GraySpace for ansible/slice.yml. Every string is !unsafe: Ansible must not template spec values.\n"
    return header + yaml.dump(variables, Dumper=_UnsafeDumper, sort_keys=True, default_flow_style=False, allow_unicode=True)
