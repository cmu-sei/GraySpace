"""Messages on the /api/v1/events websocket.

openapi/grayspace-events-v1.json is generated from Event (contract.py).
"""
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field
from typing_extensions import Annotated


class Hello(BaseModel):
    type: Literal["hello"]
    schema_: str = Field(..., alias="schema", description="grayspace-events-v1")


class Pong(BaseModel):
    type: Literal["pong"]


class FeatureUpdate(BaseModel):
    """Changed properties of one /map feature. Apply with MapLibre setFeatureState (promoteId: id) or by
    updating the deck.gl layer's data; no geometry is re-fetched."""

    type: Literal["feature.update"]
    id: str = Field(..., description="The feature's id in /map, e.g. lum-chi:are-chi")
    kind: Literal["router", "link", "site", "internet-host", "host", "attachment"]
    properties: Dict[str, Any] = Field(..., description="Only the properties that changed, e.g. state, color, diagnostics, condition, status")
    timestamp: str


class GuestUpdate(BaseModel):
    """A core guest's Proxmox status changed: a router or core service of /base/inventory.
    Routers also get a feature.update with the same status, for the map."""

    type: Literal["guest.update"]
    hostname: str
    kind: Literal["router", "client", "core-service"] = Field(..., description="client is deprecated (contract 1.18): core "
                                                               "clients were retired, and none comes")
    status: str = Field(..., description="running, stopped or missing (no longer in Proxmox)")
    previous: str = Field(..., description="The status before")
    timestamp: str


class JobUpdate(BaseModel):
    type: Literal["job.update"]
    id: str
    kind: str
    target: str
    state: str
    stage: Optional[str]
    attempt: Optional[str]
    error: Optional[str]
    timestamp: str


class InstanceUpdate(BaseModel):
    type: Literal["instance.update"]
    id: str
    state: str
    error: Optional[str]
    timestamp: str


class ActionApplied(BaseModel):
    type: Literal["action.applied"]
    instance: str
    action: str
    target: Optional[str]
    message: str
    timestamp: str


Event = Annotated[Union[Hello, Pong, FeatureUpdate, GuestUpdate, JobUpdate, InstanceUpdate, ActionApplied], Field(discriminator="type")]

EVENT_TYPES: List[str] = ["hello", "pong", "feature.update", "guest.update", "job.update", "instance.update", "action.applied"]
