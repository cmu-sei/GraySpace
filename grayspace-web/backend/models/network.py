"""Network (vNET/Zone) models."""
from typing import Optional, List
from pydantic import BaseModel, Field


class ZoneInfo(BaseModel):
    """Information about a Proxmox SDN zone."""

    zone_id: str = Field(..., description="Zone identifier")
    type: str = Field(..., description="Zone type (simple, vlan, etc.)")
    bridge: Optional[str] = Field(default=None, description="Bridge name for VLAN zones")
    nodes: Optional[str] = Field(default=None, description="Nodes where zone is available")
    pending: bool = Field(default=False, description="Whether zone has pending changes")
    description: Optional[str] = Field(default=None, description="Zone description")


class VNetInfo(BaseModel):
    """Information about a Proxmox SDN vNET."""

    vnet_id: str = Field(..., description="vNET identifier")
    zone: str = Field(..., description="Current zone assignment")
    alias: Optional[str] = Field(default=None, description="vNET alias/description")
    tag: Optional[int] = Field(default=None, description="VLAN tag if applicable")
    vlanaware: bool = Field(default=False, description="Whether VLAN aware")
    pending: bool = Field(default=False, description="Whether vNET has pending changes")


class VNetZoneChangeRequest(BaseModel):
    """Request to change a vNET's zone assignment."""

    zone_id: str = Field(..., description="Target zone ID")
    vlan_tag: Optional[int] = Field(default=None, ge=1, le=4094, description="VLAN tag (required for VLAN zones)")


class LinkNetworkInfo(BaseModel):
    """Network information for a router-to-router link."""

    link_id: str = Field(..., description="Link identifier")
    vnet_id: Optional[str] = Field(default=None, description="vNET serving this link")
    zone: Optional[str] = Field(default=None, description="Current zone assignment")
    zone_type: Optional[str] = Field(default=None, description="Zone type (simple/vlan)")
    subnet: str = Field(..., description="Link subnet")
    source_hostname: str
    target_hostname: str
    source_interface: Optional[str] = Field(default=None)
    target_interface: Optional[str] = Field(default=None)


class SDNApplyResult(BaseModel):
    """Result of applying SDN configuration changes."""

    success: bool
    message: str
    warnings: List[str] = Field(default_factory=list)


class CustomerInterfaceInfo(BaseModel):
    """Information about a customer-facing interface (ingress point)."""

    hostname: str = Field(..., description="Router hostname")
    interface_name: str = Field(..., description="Interface name (e.g., eth2)")
    customer_net: str = Field(..., description="Customer network CIDR")
    bridge: str = Field(..., description="Bridge name")
    vnet_id: Optional[str] = Field(default=None, description="vNET identifier")
    zone: Optional[str] = Field(default=None, description="Current zone assignment")
    zone_type: Optional[str] = Field(default=None, description="Zone type (simple/vlan)")
    vlan_tag: Optional[int] = Field(default=None, description="VLAN tag if in VLAN zone")


class AddDNSRecordRequest(BaseModel):
    """Request to add a DNS record to a server's zone."""

    zone: str = Field(..., description="Zone name (e.g., 'com', '.', 'example.com')")
    name: str = Field(..., description="Record name (e.g., '@', 'www', 'test.com')")
    type: str = Field(..., description="Record type (A, AAAA, NS, MX, CNAME, TXT, PTR, SRV, CAA)")
    data: str = Field(..., description="Record data (e.g., IP address, hostname)")
    ttl: int = Field(default=3600, ge=1, description="TTL in seconds")


class AddDNSRecordResult(BaseModel):
    """Result of adding a DNS record."""

    success: bool
    message: str
    warnings: List[str] = Field(default_factory=list)


class CreateDomainRequest(BaseModel):
    """Request to create a new domain with TLD delegation and hosting zone."""

    domain: str = Field(..., description="Domain name (e.g., 'example.com')")
    hosting_server: Optional[str] = Field(default=None, description="Hosting server hostname (random if not specified)")
    ip_address: Optional[str] = Field(default=None, description="IP address for @ A record")
    mx_records: Optional[str] = Field(default=None, description="MX record data (e.g., '10 mail.example.com.')")
    additional_records: Optional[List[dict]] = Field(default=None, description="Additional records to add")
    create_www: bool = Field(default=True, description="Create www CNAME pointing to @")


class CreateDomainResult(BaseModel):
    """Result of creating a new domain."""

    success: bool
    message: str
    hosting_server: Optional[str] = None
    tld_server: Optional[str] = None
    records_created: int = 0
    warnings: List[str] = Field(default_factory=list)
