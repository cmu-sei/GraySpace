"""Models for /api/v1/capabilities, /attachment-points and /slices."""
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from models.base import BaseVersionRef


class Image(BaseModel):
    """An image slice hosts can use (images/images.csv, plus local images)."""

    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9._-]{0,62}$")
    type: Literal["lxc", "vm", "oci"]
    os_family: str = Field("oci", description="alpine, ubuntu, debian, windows, or oci for application containers")
    source: str = Field(
        ...,
        description="Download URL, OCI reference, template:<vmid> (a Proxmox VM template to clone) "
        "or volume:<storage>:<path> (a template already on the node)",
    )
    cores: int = Field(1, ge=1, le=64)
    memory: int = Field(1024, ge=128, le=262144, description="MiB")
    disk: int = Field(8, ge=1, le=4096, description="GiB")
    features: List[str] = Field(default_factory=list, description="e.g. tls-kestrel")
    description: str = ""
    entrypoint: Optional[str] = Field(None, description="OCI images: the image's command, wrapped to install a certificate (tls-kestrel)")
    origin: Literal["shipped", "local"] = "shipped"


class RoleVariable(BaseModel):
    description: str
    required: bool = False
    default: Optional[Any] = None


class Role(BaseModel):
    """An Ansible role a slice host can take (ansible/roles/<name>/meta/grayspace.yml)."""

    name: str
    description: str
    os_families: List[str] = Field(..., description="Image OS families the role supports")
    image_types: Optional[List[Literal["lxc", "vm"]]] = Field(
        None, description="Image types the role runs on (since 1.21), e.g. [vm] for a role a container can't run; any when unset")
    variables: Dict[str, RoleVariable] = Field(default_factory=dict, description="role_vars the role reads")


class HostFeature(BaseModel):
    name: str
    image_type: Literal["lxc", "vm"] = Field(..., description="The image type the feature applies to")
    description: str


class DnsServer(BaseModel):
    hostname: str
    ip: Optional[str] = Field(..., description="The server's address, e.g. 8.8.8.8 for a resolver")
    router: str = Field(..., description="The router the server is hosted on")
    isp: str


class DnsCapabilities(BaseModel):
    tlds: List[str] = Field(..., description="Top-level domains served by the core's TLD servers")
    hosting: List[DnsServer] = Field(..., description="Servers that can host a slice's zones, e.g. dns-cloudflare")
    resolvers: List[DnsServer]


class CertificateAuthority(BaseModel):
    hostname: str
    name: str = Field(..., description="e.g. GrayTrust")
    ip: Optional[str] = Field(..., description="The CA's address (its ACME server and web UI)")
    router: str
    isp: str


class IspCapability(BaseModel):
    name: str
    asn: Optional[int]
    locations: List[str] = Field(..., description="Locations where the ISP has routers")


class Capacity(BaseModel):
    """The Proxmox node's current headroom."""

    node: str
    cpus: Optional[int]
    cpu_usage: Optional[float] = Field(..., description="Fraction of CPU in use, 0 to 1")
    memory_total: Optional[int] = Field(..., description="Bytes")
    memory_free: Optional[int] = Field(..., description="Bytes the node can give guests: Proxmox's available memory (free plus reclaimable cache), or free where it doesn't report available")
    storage: Optional[str] = Field(None, description="The block storage slice and core disks use (TF_VAR_proxmox_blockstore)")
    storage_total: Optional[int] = Field(None, description="Bytes")
    storage_free: Optional[int] = Field(None, description="Bytes")


class Capabilities(BaseModel):
    """What a slice can use on a core version."""

    base: BaseVersionRef
    images: List[Image] = Field(..., description="The image catalog slice hosts can use")
    roles: List[Role] = Field(..., description="Roles enclave hosts can take")
    host_features: List[HostFeature] = Field(default_factory=list, description="Deprecated (contract 1.18): features of core "
                                             "clients, which were retired; always empty. An enclave image's are its `features`",
                                             deprecated=True)
    enclave_features: List[str] = Field(
        ..., description="Enclave features this GraySpace builds: nat-site, published-host, internet-host, routed-site, dual-homed-site, "
        "oci-host, vm-host, link-conditions, crucible-player"
    )
    slice_features: List[str] = Field(..., description="Deprecated (contract 1.5): the same as enclave_features", deprecated=True)
    jobs: bool = Field(
        ..., description="Whether this GraySpace can run slice jobs (plan, create and destroy instances). False where there is no GraySpace checkout or no deployed core"
    )
    jobs_unavailable_reason: Optional[str] = None
    base_jobs: bool = Field(False, description="Whether this GraySpace can deploy, configure and destroy the core")
    crucible: bool = Field(
        False, description="Whether the deployment has a Crucible Player view (its Player step decides which guests; crucible: true pre-assigns a host or site to its default team)"
    )
    base_jobs_unavailable_reason: Optional[str] = None
    management_subnet: str = Field(..., description="Enclave address space never overlaps it")
    enclave_pool: str = Field(..., description="Where segments without an explicit CIDR are allocated from")
    slice_pool: str = Field(..., description="Deprecated (contract 1.5): the same as enclave_pool", deprecated=True)
    dns: DnsCapabilities
    cas: List[CertificateAuthority]
    isps: List[IspCapability]
    capacity: Optional[Capacity] = Field(..., description="Null when Proxmox is unavailable")


class AddressSpace(BaseModel):
    public_block: str
    free_addresses: int = Field(..., description="Addresses in the block not used by any link, loopback or network in the core")
    largest_free_prefix: Optional[str]


class AttachmentPoint(BaseModel):
    """A router whose customer network slice sites and internet hosts can join."""

    router: str
    isp: str
    asn: Optional[int]
    location: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    customer_net: str = Field(..., description="The router's customer network, which slice edges and internet hosts join")
    free_static_addresses: int = Field(
        ..., description="Free static addresses on the customer network (.2 up to the DHCP start), less the core's and live instances'. A site takes one, plus one per published host"
    )
    address_space: AddressSpace = Field(..., description="Free space in the ISP's public block, from which routed sites get their /24")


