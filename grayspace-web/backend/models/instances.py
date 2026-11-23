"""Slice instances, their allocations and manifests, and jobs."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

from models.base import BaseVersionRef

InstanceState = Literal["pending", "provisioning", "configuring", "ready", "failed", "destroying", "destroyed"]
JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
JobKind = Literal["create_instance", "destroy_instance", "plan_slice", "deploy_base", "configure_base", "destroy_base", "rollout"]

# kind -> stage -> what it does; filled by the modules that run them (stages.py, base_jobs.py, rollout.py)
STAGE_TITLES: Dict[str, Dict[str, str]] = {}


def stage_title(kind: str, stage: str) -> str:
    titles = STAGE_TITLES.get(kind, {})
    if stage in titles:
        return titles[stage]
    step, _, target = stage.partition(":")
    pattern = titles.get(f"{step}:*")
    return pattern.format(target) if pattern else stage


# --- Allocation: everything GraySpace decided, fully resolved ---


class Wan(BaseModel):
    """An edge router's WAN on a core router's customer network."""

    router: str
    isp: str
    asn: int
    location: Optional[str]
    customer_net: str
    bridge: str = Field(..., description="The core vnet (without environment_id), e.g. v332")
    ip: str = Field(..., description="The edge's static address on the customer network")
    gateway: str = Field(..., description="The core router, .1")


class SegmentAllocation(BaseModel):
    name: str
    cidr: str
    gateway: str = Field(..., description="The edge's address on the segment")
    vnet: str = Field(..., description="Slice vnet ID without environment_id, e.g. s000")


class SiteAllocation(BaseModel):
    name: str
    routed: bool
    edge: str = Field(..., description="The edge router's hostname")
    wans: List[Wan]
    segments: List[SegmentAllocation]
    public_prefix: Optional[str] = Field(None, description="Routed sites: the /24 the edge announces")
    asn: Optional[int] = Field(None, description="Routed sites: the edge's private ASN")
    resolver: str = Field(..., description="Core resolver address of the site: its edge's, and its hosts' unless the site has a domain controller (an ad-dc host)")
    crucible: bool = Field(False, description="The edge router is registered in the core's Crucible Player view")


class HostAllocation(BaseModel):
    name: str = Field(..., description="The spec name, expanded for counted hosts (ws-01)")
    spec_name: str = Field(..., description="The spec host entry it came from (ws)")
    hostname: str
    type: Literal["lxc", "oci", "vm"]
    image: str
    os_family: str
    site: Optional[str] = Field(None, description="Null for internet hosts")
    segment: Optional[str]
    bridge: str = Field(..., description="Vnet the simulated NIC joins: a slice vnet (s000) or a core customer network (v332)")
    ip: str
    prefixlen: int
    gateway: str
    public_ip: Optional[str] = Field(None, description="Published hosts: the 1:1 NAT address on the WAN. Internet hosts: their own address")
    router: Optional[str] = Field(None, description="Internet hosts: the core router they're on")
    resolver: str = Field(..., description="The host's resolver: in a site with a domain controller (an ad-dc host), the DC's address; else, and for the DC itself, the nearest core resolver")
    roles: List[str]
    labels: Dict[str, str]
    env: Dict[str, str]
    cores: int
    memory: int
    disk: int
    managed: bool = Field(..., description="Reached by Ansible over a management NIC (not OCI)")
    crucible: bool = Field(False, description="Registered in the core's Crucible Player view")


class StaticAddress(BaseModel):
    """A static address on a core customer network, taken from the .2–.19 range."""

    router: str
    ip: str
    use: str = Field(..., description="e.g. mb-hq-edge WAN, mb-web01 published, mb-news")


class Allocation(BaseModel):
    prefix: str
    sites: List[SiteAllocation]
    hosts: List[HostAllocation]
    static_addresses: List[StaticAddress]
    vnets: Dict[str, str] = Field(..., description="Slice vnet ID → alias")
    public_prefixes: List[str] = Field(default_factory=list)
    asns: List[int] = Field(default_factory=list)
    images: List[str] = Field(..., description="Catalog images the instance uses, edges included")

    def hostnames(self) -> List[str]:
        return [s.edge for s in self.sites] + [h.hostname for h in self.hosts]


# --- Manifest ---


class SliceRef(BaseModel):
    slug: str
    version: str


class ManifestSegment(BaseModel):
    name: str
    cidr: str
    gateway: str


class ManifestSite(BaseModel):
    name: str
    router: str = Field(..., description="The core router of the (first) WAN")
    isp: str
    location: Optional[str]
    wan_ip: str
    routed: bool = False
    public_prefix: Optional[str] = None
    wans: List[Wan] = Field(default_factory=list, description="Every WAN; more than one when dual-homed")
    edge: str
    segments: List[ManifestSegment]


class ManifestHost(BaseModel):
    name: str
    hostname: str
    site: Optional[str]
    segment: Optional[str]
    ip: str
    public_ip: Optional[str]
    mgmt_ip: Optional[str]
    fqdns: List[str]
    roles: List[str]
    labels: Dict[str, str]
    image: str
    status: str = Field(..., description="Proxmox status: running, stopped or unknown")


class ManifestRecord(BaseModel):
    name: str
    type: str
    data: str


class ManifestCertificate(BaseModel):
    host: str
    names: List[str]
    ca: str
    serial: Optional[str] = None


class Manifest(BaseModel):
    id: str
    slice: SliceRef = Field(..., description="Deprecated (contract 1.5): the same as `enclave`", deprecated=True)

    @computed_field(description="The enclave and version it was built from")
    @property
    def enclave(self) -> SliceRef:
        return self.slice

    base: Optional[BaseVersionRef] = Field(..., description="Deprecated (contract 1.26): the same as `core`", json_schema_extra={"deprecated": True})

    @computed_field(description="The core version it was built on")
    @property
    def core(self) -> Optional[BaseVersionRef]:
        return self.base

    state: InstanceState
    sites: List[ManifestSite]
    hosts: List[ManifestHost]
    dns: List[ManifestRecord]
    certificates: List[ManifestCertificate]


# --- What an instance created outside Terraform, so destroy can undo it ---


class CreatedDomain(BaseModel):
    domain: str
    hosting_server: str
    tld_server: str


class CreatedRecord(BaseModel):
    server: str
    zone: str
    name: str = Field(..., description="Relative to the zone, as written in the zone file")
    fqdn: str
    type: str
    data: str
    ttl: int
    origin: Literal["spec", "action"] = "spec"


class IssuedCertificate(BaseModel):
    host: str
    names: List[str]
    ca: str
    serial: Optional[str]
    cert_file: str = Field(..., description="PEM file in the instance's data directory")
    key_file: str


class LinkConditionApplied(BaseModel):
    link_id: str
    latency_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    packet_loss_percent: Optional[float] = None
    bandwidth_mbps: Optional[float] = None
    origin: Literal["spec", "action"] = "spec"


class BaseAttachment(BaseModel):
    """A BGP neighbor a routed site adds to a core router."""

    router: str
    edge: str
    ip: str
    asn: int


class InstanceRecord(BaseModel):
    id: str
    slice: SliceRef
    base: Optional[BaseVersionRef] = None
    state: InstanceState
    created: str
    updated: str
    error: Optional[str] = None
    allocation: Optional[Allocation] = None
    stages_done: List[str] = Field(default_factory=list)
    mgmt_ips: Dict[str, str] = Field(default_factory=dict)
    domains: List[CreatedDomain] = Field(default_factory=list)
    records: List[CreatedRecord] = Field(default_factory=list)
    certificates: List[IssuedCertificate] = Field(default_factory=list)
    conditions: List[LinkConditionApplied] = Field(default_factory=list)
    links_down: List[str] = Field(default_factory=list, description="Core links the instance has taken down")
    attachments: List[BaseAttachment] = Field(default_factory=list)
    manifest: Optional[Manifest] = None
    jobs: List[str] = Field(default_factory=list)

    @property
    def live(self) -> bool:
        return self.state != "destroyed"


class InstanceSummary(BaseModel):
    id: str
    slice: SliceRef = Field(..., description="Deprecated (contract 1.5): the same as `enclave`", deprecated=True)

    @computed_field(description="The enclave and version it was built from")
    @property
    def enclave(self) -> SliceRef:
        return self.slice

    state: InstanceState
    created: str
    updated: str
    error: Optional[str]
    hosts: int
    sites: int
    last_job: Optional[str]


class CreateInstanceRequest(BaseModel):
    enclave: Optional[str] = Field(None, description="Enclave slug")
    slice: Optional[str] = Field(None, description="Deprecated (contract 1.5): the enclave slug, as `enclave`", deprecated=True)
    version: Optional[str] = Field(None, description="The version to pin; defaults to the latest")
    prefix: Optional[str] = Field(None, pattern=r"^[a-z][a-z0-9]{0,7}$", description="Override the enclave's prefix, to run a second copy")

    @model_validator(mode="after")
    def _one_slug(self):
        if not (self.enclave or self.slice):
            raise ValueError("give enclave (the enclave's slug)")
        if self.enclave and self.slice and self.enclave != self.slice:
            raise ValueError("enclave and slice name different enclaves; give enclave only")
        self.enclave = self.slice = self.enclave or self.slice
        return self


class JobRecord(BaseModel):
    id: str
    kind: JobKind
    target: str = Field(..., description="Instance ID; slice slug for plan jobs; `base` for core jobs")
    slice: Optional[SliceRef] = Field(None, description="Deprecated (contract 1.5): the same as `enclave`", deprecated=True)

    @computed_field(description="The enclave the job builds, destroys or plans, when it has one")
    @property
    def enclave(self) -> Optional[SliceRef]:
        return self.slice

    state: JobState
    stages: List[str]
    stage: Optional[str] = None
    attempt: Optional[str] = Field(None, description="e.g. apply attempt 3/6")
    created: str
    started: Optional[str] = None
    finished: Optional[str] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    resume_from: Optional[str] = Field(None, description="The stage a retried job starts at")
    retry_of: Optional[str] = None
    result: Optional[Dict[str, Any]] = Field(
        None, description="Plan jobs: allocation preview, plan summary and capacity. Core jobs: the request (version, scope) and the final health. "
        "Rollouts: the plan they carry out"
    )
    by: Optional[str] = Field(None, description="Who submitted it (the control plane's X-Grayspace-User); null for the service token")
    parent: Optional[str] = Field(None, description="The rollout that ran this job, when one did")
    children: List[str] = Field(default_factory=list, description="Rollouts: the jobs they ran, in order")

    @computed_field(description="What each stage does, by stage name")
    @property
    def stage_titles(self) -> Dict[str, str]:
        return {s: stage_title(self.kind, s) for s in self.stages}


class JobAccepted(BaseModel):
    instance: Optional[str] = None
    job: JobRecord
