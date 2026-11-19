"""The slice spec: what a scenario needs at the edge of the base.

A slice says what an exercise needs (sites, segments, hosts, DNS names, certificates). GraySpace
decides how to build it: routers, addresses, vnets, images, configuration. The spec is
GraySpace's own schema; GHOSTS maps its scenario documents onto it in its GraySpace provider.
openapi/grayspace-slice-v1.json is generated from these models (contract.py).
"""
import hashlib
import ipaddress
import json
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# A name that becomes part of a hostname: lowercase letters, digits and inner hyphens.
LABEL = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
SLUG = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
PREFIX = r"^[a-z][a-z0-9]{0,7}$"
DOMAIN = r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
FQDN = r"^(\*\.)?([a-z0-9_]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"

RESERVED_SLUGS = {"base"}
HOSTNAME = r"([a-z0-9_]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.?"


def record_data_problem(rtype: str, data: str) -> Optional[str]:
    """Record data goes into a zone file line: one line, in the form its type needs. None if fine."""
    import re

    if "\n" in data or "\r" in data or len(data) > 1024:
        return "Record data must be a single line"
    if rtype == "A":
        try:
            ipaddress.IPv4Address(data)
        except ValueError:
            return f"'{data}' is not an IPv4 address"
    elif rtype == "CNAME" and not re.fullmatch(HOSTNAME, data):
        return f"'{data}' is not a host name"
    elif rtype == "MX" and not re.fullmatch(r"\d{1,5} " + HOSTNAME, data):
        return f"'{data}' is not 'preference host', e.g. '10 mail.example.com.'"
    elif rtype == "TXT" and ('"' in data.strip('"') or ";" in data):
        return "TXT data can't contain quotes or semicolons"
    return None


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Attach(SpecModel):
    """Where something joins the base: an ISP and/or location, or an exact router."""

    isp: Optional[str] = Field(None, description="ISP name, e.g. Lumen")
    location: Optional[str] = Field(None, description="Location name, e.g. Chicago")
    router: Optional[str] = Field(None, description="An exact router from /attachment-points, e.g. lum-chi")

    @model_validator(mode="after")
    def _something(self):
        if not (self.isp or self.location or self.router):
            raise ValueError("give an isp, a location, or a router")
        return self


class Segment(SpecModel):
    name: str = Field(..., pattern=LABEL, max_length=24)
    cidr: Optional[str] = Field(
        None, description="IPv4 network, e.g. 192.168.20.0/24. Omitted or 'auto': allocated by GraySpace"
    )

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, value):
        if value in (None, "auto"):
            return None
        try:
            net = ipaddress.IPv4Network(value)
        except ValueError as e:
            raise ValueError(f"not an IPv4 network: {e}")
        return str(net)


class Site(SpecModel):
    """An enterprise site: an edge router on the base and the segments behind it."""

    name: str = Field(..., pattern=LABEL, max_length=24)
    attach: Union[Attach, List[Attach]] = Field(
        ..., description="Where the edge's WAN joins the base. A list dual-homes the site (routed sites only)"
    )
    routed: bool = Field(
        False,
        description="false: the edge NATs the site behind one address on a base customer network. "
        "true: the site gets a public /24 from the ISP's block and the edge peers with the base over eBGP",
    )
    segments: List[Segment] = Field(..., min_length=1)
    crucible: bool = Field(False, description="Register the site's edge router in the base's Crucible Player view")

    @field_validator("attach")
    @classmethod
    def _some_attachment(cls, value):
        if isinstance(value, list) and not value:
            raise ValueError("give at least one attachment")
        return value

    @property
    def attachments(self) -> List[Attach]:
        return self.attach if isinstance(self.attach, list) else [self.attach]


class Size(SpecModel):
    cores: int = Field(..., ge=1, le=64)
    memory: int = Field(..., ge=128, le=262144, description="MiB")
    disk: int = Field(..., ge=1, le=4096, description="GiB")


class Host(SpecModel):
    """A host in a site segment, or an internet host placed on the base itself."""

    name: str = Field(..., pattern=LABEL, max_length=40)
    count: Optional[int] = Field(None, ge=1, le=500, description="Make this many hosts, named <name>-01 …")
    segment: Optional[str] = Field(None, description="The site segment the host joins")
    internet: Optional[Attach] = Field(
        None, description="Place the host directly on a base router's customer network, outside any site"
    )
    image: str = Field(..., description="An image from /capabilities")
    roles: List[str] = Field(default_factory=list, description="Roles from /capabilities")
    size: Optional[Size] = Field(None, description="Defaults to the image's size")
    labels: Dict[str, str] = Field(default_factory=dict, description="Free-form, returned in the manifest (e.g. ghosts.pool)")
    env: Dict[str, str] = Field(default_factory=dict, description="Environment variables for OCI images")
    publish: bool = Field(False, description="Give a site host a public address on its site's WAN (1:1 NAT)")
    crucible: bool = Field(
        False, description="Register the host (every copy, with count) in the base's Crucible Player view, for participants to see"
    )

    @model_validator(mode="after")
    def _placement(self):
        if bool(self.segment) == bool(self.internet):
            raise ValueError("give exactly one of segment or internet")
        if self.internet and self.publish:
            raise ValueError("internet hosts are public already; publish applies to site hosts")
        return self

    def expanded_names(self) -> List[str]:
        if self.count is None:
            return [self.name]
        width = max(2, len(str(self.count)))
        return [f"{self.name}-{i:0{width}d}" for i in range(1, self.count + 1)]


class DnsDomain(SpecModel):
    domain: str = Field(..., pattern=DOMAIN, max_length=253, description="e.g. meridian-bank.com; its TLD must be in the base")
    hosting_server: str = Field(..., description="A base hosting DNS server, e.g. dns-cloudflare")


class DnsRecord(SpecModel):
    name: str = Field(..., pattern=FQDN, max_length=253, description="Fully qualified, inside one of the slice's domains")
    type: Literal["A", "CNAME", "TXT", "MX"] = "A"
    host: Optional[str] = Field(None, description="A records: the slice host it resolves to (its reachable address)")
    data: Optional[str] = Field(None, description="Record data, for records that don't name a host")
    ttl: int = Field(3600, ge=60, le=604800)

    @model_validator(mode="after")
    def _target(self):
        if bool(self.host) == bool(self.data):
            raise ValueError("give exactly one of host or data")
        if self.host and self.type != "A":
            raise ValueError("host applies to A records")
        if self.data is not None:
            problem = record_data_problem(self.type, self.data)
            if problem:
                raise ValueError(problem)
        return self


class Dns(SpecModel):
    domains: List[DnsDomain] = Field(default_factory=list)
    records: List[DnsRecord] = Field(default_factory=list)


class Tls(SpecModel):
    host: str = Field(..., description="The slice host that serves the certificate")
    names: List[str] = Field(..., min_length=1, description="Names on the certificate")
    ca: str = Field(..., description="A base CA, e.g. ca-graytrust")

    @field_validator("names")
    @classmethod
    def _names(cls, names):
        import re

        for name in names:
            if len(name) > 253 or not re.match(FQDN, name):
                raise ValueError(f"'{name}' is not a fully qualified name")
        return names


class LinkCondition(SpecModel):
    """Conditions on a base link, applied while the instance exists."""

    latency_ms: Optional[float] = Field(None, ge=0, le=10000)
    jitter_ms: Optional[float] = Field(None, ge=0, le=5000)
    packet_loss_percent: Optional[float] = Field(None, ge=0, le=100)
    bandwidth_mbps: Optional[float] = Field(None, ge=0.1, le=10000)


class Conditions(SpecModel):
    links: Dict[str, LinkCondition] = Field(default_factory=dict, description="Keyed by link ID (r1:r2), as in /map")


class SliceSpec(SpecModel):
    """A named, versioned addition to the base."""

    apiVersion: Literal["grayspace/v1"]
    kind: Literal["Slice", "Enclave"] = Field(
        ..., description="Enclave and Slice are the same (contract 1.5; Slice is deprecated). Stored and returned as Slice until 2.0"
    )
    slug: str = Field(..., pattern=SLUG)
    prefix: str = Field(..., pattern=PREFIX, description="Hostname prefix, unique among instances")
    description: str = ""
    owner: Optional[str] = Field(None, description="Who manages the slice, e.g. ghosts. Informational")
    sites: List[Site] = Field(default_factory=list)
    hosts: List[Host] = Field(default_factory=list)
    dns: Dns = Field(default_factory=Dns)
    tls: List[Tls] = Field(default_factory=list)
    conditions: Conditions = Field(default_factory=Conditions)
    role_vars: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict, description="Variables per role, passed to the role unchanged (e.g. ghosts-client.api_url)"
    )

    @field_validator("slug")
    @classmethod
    def _not_reserved(cls, slug):
        if slug in RESERVED_SLUGS:
            raise ValueError(f"'{slug}' is reserved")
        return slug

    def canonical(self) -> Dict[str, Any]:
        """The spec without defaults, so equal specs hash equal however they were written."""
        # kind: Enclave and kind: Slice are one spec, so they hash to one version
        return {**self.model_dump(mode="json", exclude_defaults=True), "kind": "Slice"}

    def version(self) -> str:
        """The content hash that names this version."""
        text = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


class Finding(BaseModel):
    """A problem or note about a spec. An error blocks an instance; a warning doesn't."""

    code: str = Field(..., description="e.g. NO_STATIC_ADDRESSES; see docs/slices.md")
    severity: Literal["error", "warning"]
    path: str = Field(..., description="Where in the spec, e.g. sites[0].attach")
    message: str
    suggestion: Optional[Any] = Field(None, description="A value that would resolve the finding if put at path")


class SliceVersion(BaseModel):
    version: str = Field(..., description="sha256:<hex> of the spec's content")
    created: str


class SliceSummary(BaseModel):
    slug: str
    kind: Literal["base", "slice"]
    description: str
    owner: Optional[str] = None
    latest: Optional[str] = Field(None, description="The most recently stored version")
    versions: List[SliceVersion] = Field(default_factory=list)
    instances: List[str] = Field(default_factory=list, description="Live instances of the slice")


class StoredSlice(BaseModel):
    """A stored slice version (GET /slices/{slug}?version=)."""

    slug: str
    version: str
    spec: SliceSpec


class PutSliceResult(BaseModel):
    slug: str
    version: str
    created: bool = Field(..., description="False when this exact content was already stored")
    findings: List[Finding]


class ValidateResult(BaseModel):
    valid: bool = Field(..., description="No errors (warnings allowed)")
    version: str
    findings: List[Finding]
