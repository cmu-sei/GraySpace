"""The map layer: GraySpace's map as GeoJSON that any map widget can draw.

The server does the drawing logic, so GraySpace's Leaflet map and the GHOSTS MapLibre/deck.gl map
agree on what to draw:

- routers in the same city fanned out in a circle (topology_service._apply_location_offsets)
- links as densified great-circle LineStrings, split at the 180° meridian into a MultiLineString
  (RFC 7946 §3.1.9)
- ISP colors assigned in the base version's ISP order, and link colors from state and type
- slice sites and internet hosts near their attachment routers, with attachment lines

Every feature has a stable `id` (also in properties, for MapLibre's promoteId) and a `kind`. Live
updates (/ws link_state_update, /api/v1/events feature.update) carry changed properties by id.
"""
import math
from typing import Dict, List, Optional, Tuple

from services.slices.base import links as base_links

# The palette utils/colors.ts used, now assigned by ISP order in the base instead of draw order.
ISP_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#34495e", "#e67e22",
    "#95a5a6", "#16a085", "#c0392b", "#2980b9", "#27ae60", "#8e44ad", "#f1c40f",
]
LINK_TYPE_COLORS = {"INTERNAL_LINK": "#5b7ea8", "PEERS_WITH": "#2ecc71", "PROVIDES_TRANSIT_TO": "#e67e22"}
LINK_STATE_COLORS = {"down": "#e74c3c", "shutdown": "#f39c12"}
SLICE_COLOR = "#ff00aa"
# Degrees between densified points along a link
STEP_DEGREES = 2.0
# How far slice sites and internet hosts sit from their router, in degrees
SLICE_OFFSET = 0.6
OFFSET_RADIUS = 0.3


def link_color(state: Optional[str], link_type: str) -> str:
    return LINK_STATE_COLORS.get(state) or LINK_TYPE_COLORS.get(link_type, "#5b7ea8")


def _wrap(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0 if not -180.0 <= lon <= 180.0 else lon


def _point(lon: float, lat: float) -> List[float]:
    return [round(_wrap(lon), 5), round(max(-90.0, min(90.0, lat)), 5)]


def great_circle(start: Tuple[float, float], end: Tuple[float, float], step: float = STEP_DEGREES) -> List[List[float]]:
    """Points (lon, lat) along the great circle from start to end, both included."""
    lon1, lat1, lon2, lat2 = map(math.radians, (start[0], start[1], end[0], end[1]))
    v1 = (math.cos(lat1) * math.cos(lon1), math.cos(lat1) * math.sin(lon1), math.sin(lat1))
    v2 = (math.cos(lat2) * math.cos(lon2), math.cos(lat2) * math.sin(lon2), math.sin(lat2))
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(v1, v2))))
    angle = math.acos(dot)
    n = max(1, math.ceil(math.degrees(angle) / step))
    if angle < 1e-9:
        return [_point(*start), _point(*end)]
    out = []
    for i in range(n + 1):
        f = i / n
        a = math.sin((1 - f) * angle) / math.sin(angle)
        b = math.sin(f * angle) / math.sin(angle)
        x, y, z = (a * p + b * q for p, q in zip(v1, v2))
        out.append(_point(math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))))
    out[0], out[-1] = _point(*start), _point(*end)
    return out


def split_antimeridian(points: List[List[float]]) -> List[List[List[float]]]:
    """Split a line wherever it jumps across the 180° meridian, ending and starting each part on it."""
    parts = [[points[0]]]
    for prev, cur in zip(points, points[1:]):
        if abs(cur[0] - prev[0]) > 180:
            # Unwrap cur next to prev, find where the segment meets ±180, and cut there.
            side = 180.0 if prev[0] > 0 else -180.0
            cur_lon = cur[0] + (360.0 if side > 0 else -360.0)
            f = (side - prev[0]) / (cur_lon - prev[0]) if cur_lon != prev[0] else 0.5
            lat = round(prev[1] + f * (cur[1] - prev[1]), 5)
            parts[-1].append([side, lat])
            parts.append([[-side, lat]])
        parts[-1].append(cur)
    return [p for p in parts if len(p) >= 2]


def line_geometry(start: Tuple[float, float], end: Tuple[float, float]) -> dict:
    parts = split_antimeridian(great_circle(start, end))
    if len(parts) == 1:
        return {"type": "LineString", "coordinates": parts[0]}
    return {"type": "MultiLineString", "coordinates": parts}


def _offsets(routers: List[dict]) -> Dict[str, Tuple[float, float]]:
    """{hostname: (lon, lat)} with routers sharing a location fanned out, as the UI always has."""
    from services.topology_service import topology_service

    placed = topology_service._apply_location_offsets([{**r, "location": dict(r["location"])} for r in routers])
    return {r["hostname"]: (r["location"]["longitude"], r["location"]["latitude"]) for r in placed}


def _feature(feature_id: str, kind: str, geometry: dict, **properties) -> dict:
    return {"type": "Feature", "id": feature_id, "geometry": geometry, "properties": {"id": feature_id, "kind": kind, **properties}}


def build(tfvars: dict, isp_order: List[str], link_states: Dict[str, dict], router_status: Optional[Dict[str, str]],
          instances: List, instance: Optional[str] = None) -> dict:
    """The FeatureCollection. router_status is {hostname: Proxmox status}, or None when Proxmox is
    unavailable. instances are live InstanceRecords; `instance` limits slice features to one."""
    routers = [
        {"hostname": h, "location": r["location"], "isp": r.get("isp_name"), "asn": r.get("asn")}
        for h, r in (tfvars.get("routers") or {}).items()
        if r.get("location") and r["location"].get("latitude") is not None
    ]
    order = list(isp_order) + [r["isp"] for r in routers if r["isp"] not in isp_order]
    colors = {}
    for isp in order:
        colors.setdefault(isp, ISP_COLORS[len(colors) % len(ISP_COLORS)])
    position = _offsets(routers)
    features = []
    for r in routers:
        status = "unknown" if router_status is None else router_status.get(r["hostname"], "missing")
        features.append(_feature(r["hostname"], "router", {"type": "Point", "coordinates": _point(*position[r["hostname"]])},
                                 isp=r["isp"], asn=r["asn"], location=r["location"].get("name"), color=colors.get(r["isp"]), status=status))
    for link_id, link in base_links(tfvars).items():
        if link["source"] not in position or link["target"] not in position:
            continue
        state = (link_states.get(link_id) or {}).get("state", "unknown")
        features.append(_feature(link_id, "link", line_geometry(position[link["source"]], position[link["target"]]),
                                 type=link["type"], state=state, color=link_color(state, link["type"]),
                                 source=link["source"], target=link["target"]))

    # Slice features: around each router, spread whatever slices attach to it.
    attached: Dict[str, int] = {}
    for inst in instances:
        if instance and inst.id != instance:
            continue
        alloc = inst.allocation
        if not alloc:
            continue
        for site in alloc.sites:
            first = site.wans[0].router
            if first not in position:
                continue
            spot = _around(position[first], attached, first)
            fid = f"{inst.id}/{site.name}"
            features.append(_feature(fid, "site", {"type": "Point", "coordinates": spot}, instance=inst.id, name=site.name,
                                     router=first, routers=[w.router for w in site.wans], wan_ip=site.wans[0].ip,
                                     routed=site.routed, edge=site.edge, color=SLICE_COLOR, state=inst.state))
            for wan in site.wans:
                if wan.router in position:
                    features.append(_feature(f"{fid}:{wan.router}", "attachment", line_geometry(tuple(spot), position[wan.router]),
                                             instance=inst.id, router=wan.router, **{"from": fid}))
        fqdns = {}
        for rec in inst.records:
            fqdns.setdefault(rec.data, []).append(rec.fqdn)
        for h in alloc.hosts:
            if h.site is not None or h.router not in position:
                continue
            spot = _around(position[h.router], attached, h.router)
            fid = f"{inst.id}/{h.name}"
            features.append(_feature(fid, "internet-host", {"type": "Point", "coordinates": spot}, instance=inst.id, name=h.name,
                                     hostname=h.hostname, router=h.router, ip=h.ip, fqdns=sorted(fqdns.get(h.ip, [])),
                                     color=SLICE_COLOR, state=inst.state))
            features.append(_feature(f"{fid}:{h.router}", "attachment", line_geometry(tuple(spot), position[h.router]),
                                     instance=inst.id, router=h.router, **{"from": fid}))
    return {"type": "FeatureCollection", "features": features}


def _around(center: Tuple[float, float], attached: Dict[str, int], router: str) -> List[float]:
    """The next free spot on a small circle around a router, so a router's slices don't overlap."""
    i = attached.get(router, 0)
    attached[router] = i + 1
    angle = math.radians(135 + 60 * i)
    return _point(center[0] + SLICE_OFFSET * math.cos(angle), center[1] + SLICE_OFFSET * math.sin(angle))
