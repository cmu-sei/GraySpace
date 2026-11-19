"""Traffic shaping models."""
from typing import Optional
from pydantic import BaseModel, Field


class TrafficConfig(BaseModel):
    """Traffic shaping configuration for an interface."""

    latency_ms: Optional[float] = Field(
        default=None,
        ge=0,
        le=10000,
        description="Latency in milliseconds (0-10000)"
    )
    jitter_ms: Optional[float] = Field(
        default=None,
        ge=0,
        le=5000,
        description="Jitter in milliseconds (0-5000)"
    )
    bandwidth_mbps: Optional[float] = Field(
        default=None,
        ge=0.1,
        le=10000,
        description="Bandwidth limit in Mbps (0.1-10000)"
    )
    packet_loss_percent: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="Packet loss percentage (0-100)"
    )
    correlation_percent: Optional[float] = Field(
        default=25,
        ge=0,
        le=100,
        description="Loss correlation percentage (0-100)"
    )

    def has_any_config(self) -> bool:
        """Check if any traffic shaping is configured."""
        return any([
            self.latency_ms is not None and self.latency_ms > 0,
            self.jitter_ms is not None and self.jitter_ms > 0,
            self.bandwidth_mbps is not None,
            self.packet_loss_percent is not None and self.packet_loss_percent > 0
        ])


class InterfaceTrafficConfig(BaseModel):
    """Traffic configuration for a specific interface."""

    interface: str = Field(..., description="Network interface name (e.g., eth0)")
    config: TrafficConfig = Field(default_factory=TrafficConfig)
    active: bool = Field(default=False, description="Whether traffic shaping is currently active")


class InterfaceInfo(BaseModel):
    """Information about a network interface."""

    name: str = Field(..., description="Interface name")
    ip_address: Optional[str] = Field(default=None, description="IP address assigned to interface")
    mac_address: Optional[str] = Field(default=None, description="MAC address")
    state: str = Field(default="unknown", description="Interface state (up/down/unknown)")
    traffic_config: Optional[TrafficConfig] = Field(default=None, description="Current traffic shaping config")


class TrafficConfigRequest(BaseModel):
    """Request body for applying traffic configuration."""

    latency_ms: Optional[float] = Field(default=None, ge=0, le=10000)
    jitter_ms: Optional[float] = Field(default=None, ge=0, le=5000)
    bandwidth_mbps: Optional[float] = Field(default=None, ge=0.1, le=10000)
    packet_loss_percent: Optional[float] = Field(default=None, ge=0, le=100)


class LinkTrafficConfig(BaseModel):
    """Traffic configuration for a router-to-router link (applied symmetrically)."""

    link_id: str = Field(..., description="Link identifier (format: router1:router2)")
    config: TrafficConfig = Field(default_factory=TrafficConfig)
    source_hostname: str = Field(..., description="Source router hostname")
    target_hostname: str = Field(..., description="Target router hostname")
    source_interface: str = Field(..., description="Source router interface (e.g., eth0)")
    target_interface: str = Field(..., description="Target router interface (e.g., eth0)")
