"""GET /api/v1/map: the map layer as a GeoJSON FeatureCollection (RFC 7946)."""
from typing import Any, Dict, List, Literal, Union

from pydantic import BaseModel, Field

Position = List[float]


class Point(BaseModel):
    type: Literal["Point"]
    coordinates: Position = Field(..., description="[longitude, latitude]")


class LineString(BaseModel):
    type: Literal["LineString"]
    coordinates: List[Position]


class MultiLineString(BaseModel):
    type: Literal["MultiLineString"]
    coordinates: List[List[Position]] = Field(..., description="A line split at the 180° meridian")


class Feature(BaseModel):
    type: Literal["Feature"]
    id: str = Field(..., description="Stable: router hostname, link ID r1:r2, <instance>/<site>, <instance>/<host>, or <from>:<router> for attachments")
    geometry: Union[Point, LineString, MultiLineString] = Field(..., discriminator="type")
    properties: Dict[str, Any] = Field(
        ...,
        description="Always id and kind (router, link, site, internet-host, attachment). router: isp, asn, location, color, status. "
        "link: type, state, color, source, target. site: instance, name, router, routers, wan_ip, routed, edge, color, state. "
        "internet-host: instance, name, hostname, router, ip, fqdns, color, state. attachment: instance, router, from",
    )


class FeatureCollection(BaseModel):
    type: Literal["FeatureCollection"]
    features: List[Feature]
