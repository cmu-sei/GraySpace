"""Topology model: load a base version's CSVs directly, without Neo4j.

The CSVs keep the neo4j-admin header format (`name:ID(Router)`, `:START_ID(Router)`, `asn:int`,
`roles:string[]`, ...). `load()` reads them into a Topology, validating as it goes. `dump()`
writes a Topology back out in canonical CSV form. `query_rows()` returns the rows
generate_tfvars.py (formerly neo4j_parser.py) used to get from Neo4j's Cypher queries, with the
same values and in the same order, so its tfvars logic is unchanged.

See tests/golden/README.md.
"""

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

# The files of a base version, in neo4j-admin import order: (file, node label) for nodes and
# (file, relationship type) for relationships. connections.csv carries its type per row.
NODE_FILES = [
    ("isps.csv", "ISP"),
    ("locations.csv", "Location"),
    ("routers.csv", "Router"),
    ("core_dns.csv", "CoreService"),
    ("core_ca.csv", "CoreService"),
    ("os_images.csv", "Image"),
]
REL_FILES = [
    ("located_in.csv", "LOCATED_IN"),
    ("operated_by.csv", "OPERATED_BY"),
    ("connections.csv", None),
    ("hosted_by.csv", "HOSTED_BY"),
]

# Base clients were retired (base version 2026.11): what attaches to the core is an enclave
RETIRED_FILES = ["clients.csv", "client_of.csv"]

# neo4j-admin's default array delimiter. Values such as "0,10" therefore load as ["0,10"];
# the parser and frr.conf.j2 split them again. Kept until that quirk is cleaned up deliberately.
ARRAY_DELIMITER = ";"

TIER1_ISPS = ["Lumen", "NTT", "Arelion"]


class LoadError(Exception):
    """The CSVs don't form a valid topology. Carries every problem found, not just the first."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class Node:
    label: str
    id: str
    props: dict
    source: str = ""  # the CSV file the node is written to


@dataclass
class Rel:
    type: str
    start: str
    end: str
    props: dict
    source: str = ""


@dataclass
class Topology:
    # Nodes by label, then ID, in CSV order. Relationships in CSV order.
    nodes: dict = field(default_factory=dict)
    rels: list = field(default_factory=list)
    # Each file's header cells, without unnamed empty columns.
    headers: dict = field(default_factory=dict)

    def of(self, label):
        return list(self.nodes.get(label, {}).values())

    def out(self, node_id, rel_type=None):
        return [r for r in self.rels if r.start == node_id and rel_type in (None, r.type)]

    def into(self, node_id, rel_type=None):
        return [r for r in self.rels if r.end == node_id and rel_type in (None, r.type)]


def _convert(raw, kind):
    if kind in ("string", ""):
        return raw
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "string[]":
        return raw.split(ARRAY_DELIMITER)
    raise ValueError(f"unsupported column type '{kind}'")


def _columns(header):
    """Parse a neo4j-admin header into (name, kind, group) per column; kind may be ID/START_ID/END_ID/TYPE."""
    cols = []
    for cell in header:
        name, _, kind = cell.partition(":")
        group = None
        if "(" in kind:
            kind, _, group = kind.partition("(")
            group = group.rstrip(")")
        cols.append((name, kind.lower() if kind not in ("ID", "START_ID", "END_ID", "TYPE") else kind, group))
    return cols


def _read(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    return rows[0], _columns(rows[0]), rows[1:]


def load(csv_dir):
    """Load a base version's CSVs into a Topology. Raises LoadError listing every problem."""
    csv_dir = Path(csv_dir)
    topo = Topology()
    problems = []
    groups = {}  # ID group -> {id: label}

    missing = [f for f, _ in NODE_FILES + REL_FILES if not (csv_dir / f).is_file()]
    if missing:
        raise LoadError([f"{csv_dir}: missing {f}" for f in missing])
    retired = [f for f in RETIRED_FILES if (csv_dir / f).is_file()]
    if retired:
        raise LoadError([f"{csv_dir}: {f} is no longer read: base clients were retired, and hosts on the "
                         "simulated internet are enclaves (docs/slices.md)" for f in retired])

    for filename, label in NODE_FILES:
        header, cols, rows = _read(csv_dir / filename)
        topo.headers[filename] = [cell for cell in header if cell]
        for line, row in enumerate(rows, start=2):
            if not any(row):
                continue
            where = f"{filename}:{line}"
            node_id, props = None, {}
            for (name, kind, group), raw in zip(cols, row):
                if kind == "ID":
                    node_id = raw
                    if name:
                        props[name] = raw
                    id_group = group
                elif name and raw != "":
                    try:
                        props[name] = _convert(raw, kind)
                    except ValueError as e:
                        problems.append(f"{where}: column '{name}': {e}")
            if not node_id:
                problems.append(f"{where}: missing ID")
                continue
            if node_id in groups.setdefault(id_group, {}):
                problems.append(f"{where}: duplicate {id_group} ID '{node_id}'")
                continue
            groups[id_group][node_id] = label
            topo.nodes.setdefault(label, {})[node_id] = Node(label, node_id, props, filename)

    for filename, rel_type in REL_FILES:
        header, cols, rows = _read(csv_dir / filename)
        topo.headers[filename] = [cell for cell in header if cell]
        for line, row in enumerate(rows, start=2):
            if not any(row):
                continue
            where = f"{filename}:{line}"
            start = end = None
            row_type, props = rel_type, {}
            for (name, kind, group), raw in zip(cols, row):
                if kind == "START_ID":
                    start, start_group = raw, group
                elif kind == "END_ID":
                    end, end_group = raw, group
                elif kind == "TYPE":
                    row_type = raw
                elif name and raw != "":
                    try:
                        props[name] = _convert(raw, kind)
                    except ValueError as e:
                        problems.append(f"{where}: column '{name}': {e}")
            ok = True
            for ref, group in ((start, start_group), (end, end_group)):
                if ref not in groups.get(group, {}):
                    problems.append(f"{where}: unknown {group} '{ref}'")
                    ok = False
            if not row_type:
                problems.append(f"{where}: missing relationship type")
                ok = False
            if ok:
                topo.rels.append(Rel(row_type, start, end, props, filename))

    if problems:
        raise LoadError(problems)
    return topo


def csv_sha256(csv_dir):
    """A base version's identity: the hash of its CSVs, as `sha256sum *.csv | sha256sum` gives it
    when run in the directory. Independent of where the directory lives."""
    listing = "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(Path(csv_dir).glob("*.csv"))
    )
    return hashlib.sha256(listing.encode()).hexdigest()


def _format(value, kind):
    if value is None:
        return ""
    if kind == "string[]":
        return ARRAY_DELIMITER.join(value)
    if kind == "float":
        return repr(value)
    return str(value)


def dump(topo, csv_dir):
    """Write a Topology as CSVs, one file per entry in NODE_FILES and REL_FILES.

    The output is canonical: UTF-8 without a BOM, LF line endings, a final newline, quoting only
    where a value needs it, and rows in model order. Loading a canonical directory and dumping it
    again reproduces it byte for byte.
    """
    csv_dir = Path(csv_dir)
    csv_dir.mkdir(parents=True, exist_ok=True)
    nodes = [n for label in topo.nodes.values() for n in label.values()]
    for filename, _ in NODE_FILES + REL_FILES:
        header = topo.headers[filename]
        cols = _columns(header)
        with open(csv_dir / filename, "w", newline="", encoding="utf-8") as f:
            out = csv.writer(f, lineterminator="\n")
            out.writerow(header)
            for node in (n for n in nodes if n.source == filename):
                out.writerow(
                    node.id if kind == "ID" else _format(node.props.get(name), kind)
                    for name, kind, _ in cols
                )
            for rel in (r for r in topo.rels if r.source == filename):
                fixed = {"START_ID": rel.start, "END_ID": rel.end, "TYPE": rel.type}
                out.writerow(
                    fixed[kind] if kind in fixed else _format(rel.props.get(name), kind)
                    for name, kind, _ in cols
                )


# ---- the parser's queries, answered from the model ---------------------------------------------
#
# Each function returns what the matching Cypher query returned before Neo4j was removed (the
# queries are in neo4j_parser.py's git history, now generate_tfvars.py), in the order Neo4j's query plan produced it. Neo4j walks a node's relationships newest first, hence the
# reversed() calls; tests/golden/README.md records the rules.


def _prop(node, name):
    return node.props.get(name)


def _isp_of(topo, router_id):
    rels = topo.out(router_id, "OPERATED_BY")
    return topo.nodes["ISP"][rels[0].end] if rels else None


def _location_of(topo, router_id):
    rels = topo.out(router_id, "LOCATED_IN")
    return topo.nodes["Location"][rels[0].end] if rels else None


def _router_row(router, isp, loc):
    return {
        "hostname": router.id,
        "loopback_ip": _prop(router, "loopback_ip"),
        "roles": _prop(router, "roles"),
        "ospf_areas": _prop(router, "ospf_areas"),
        "customer_net": _prop(router, "customer_net"),
        "isp_name": isp.id,
        "asn": _prop(isp, "asn"),
        "public_block": _prop(isp, "public_block"),
        "latitude": _prop(loc, "latitude"),
        "longitude": _prop(loc, "longitude"),
    }


def _routers(topo, isp_names):
    # ISP scan, then each ISP's routers (newest OPERATED_BY first), then their location.
    rows = []
    for isp in topo.of("ISP"):
        if isp_names is not None and isp.id not in isp_names:
            continue
        for rel in reversed(topo.into(isp.id, "OPERATED_BY")):
            router = topo.nodes.get("Router", {}).get(rel.start)
            if router is None:
                continue
            for loc_rel in reversed(topo.out(router.id, "LOCATED_IN")):
                rows.append(_router_row(router, isp, topo.nodes["Location"][loc_rel.end]))
    return rows


def _attached(topo, label, rel_type, keep, isp_names, columns):
    # Node scan (CSV order), then each node's outgoing rel to a router (newest first),
    # then, for tier1, the router's ISP.
    rows = []
    for node in topo.of(label):
        if not keep(node):
            continue
        for rel in reversed(topo.out(node.id, rel_type)):
            router = topo.nodes.get("Router", {}).get(rel.end)
            if router is None:
                continue
            if isp_names is not None:
                isp = _isp_of(topo, router.id)
                if isp is None or isp.id not in isp_names:
                    continue
            rows.append(columns(node, rel, router))
    return rows


def _core_columns(extra):
    def columns(cs, rel, router):
        return {
            "hostname": cs.id,
            "image": _prop(cs, "image"),
            "role": _prop(cs, "role"),
            extra: _prop(cs, extra),
            "cores": _prop(cs, "cores"),
            "memory": _prop(cs, "memory"),
            "disk": _prop(cs, "disk"),
            "ip_address": rel.props.get("service_ip"),
            "router": router.id,
            "subnet": rel.props.get("subnet"),
        }

    return columns


def _link_row(topo, rel, r1, r2):
    loc1, loc2 = _location_of(topo, r1.id), _location_of(topo, r2.id)
    return {
        "r1_hostname": r1.id,
        "r2_hostname": r2.id,
        "r1_ip": rel.props.get("start_ip"),
        "r2_ip": rel.props.get("end_ip"),
        "subnet": rel.props.get("subnet"),
        "ospf_area": rel.props.get("ospf_area"),
        "link_type": rel.type,
        "r1_lat": _prop(loc1, "latitude"),
        "r1_lon": _prop(loc1, "longitude"),
        "r2_lat": _prop(loc2, "latitude"),
        "r2_lon": _prop(loc2, "longitude"),
    }


def _links_into(topo, r2, isp_names):
    rows = []
    for rel in reversed(topo.into(r2.id)):
        r1 = topo.nodes.get("Router", {}).get(rel.start)
        if r1 is None:
            continue
        if isp_names is not None:
            isp = _isp_of(topo, r1.id)
            if isp is None or isp.id not in isp_names:
                continue
        if _location_of(topo, r1.id) is None or _location_of(topo, r2.id) is None:
            continue
        rows.append(_link_row(topo, rel, r1, r2))
    return rows


def _links(topo, isp_names):
    rows = []
    if isp_names is None:
        # Location scan, then the routers located there, then the links ending at each.
        for loc in topo.of("Location"):
            for loc_rel in reversed(topo.into(loc.id, "LOCATED_IN")):
                r2 = topo.nodes.get("Router", {}).get(loc_rel.start)
                if r2 is not None:
                    rows += _links_into(topo, r2, None)
    else:
        # ISP scan, then each ISP's routers, then the links ending at each.
        for isp in topo.of("ISP"):
            if isp.id not in isp_names:
                continue
            for op_rel in reversed(topo.into(isp.id, "OPERATED_BY")):
                r2 = topo.nodes.get("Router", {}).get(op_rel.start)
                if r2 is not None:
                    rows += _links_into(topo, r2, isp_names)
    return rows


def _router_locations(topo, isp_names):
    rows = []
    for router in topo.of("Router"):
        if isp_names is not None:
            isp = _isp_of(topo, router.id)
            if isp is None or isp.id not in isp_names:
                continue
        for rel in topo.out(router.id, "LOCATED_IN"):
            rows.append({"hostname": router.id, "location_name": rel.end})
    return rows


def query_rows(topo, scope):
    """The parser's query results for a scope ('full' or 'tier1'), keyed by query name."""
    if scope not in ("full", "tier1"):
        raise ValueError(f"unknown scope '{scope}'")
    isp_names = None if scope == "full" else TIER1_ISPS
    # Cypher's `role <> 'ca'` is null, so excluded, for a missing role.
    is_ca = lambda cs: _prop(cs, "role") == "ca"
    not_ca = lambda cs: _prop(cs, "role") is not None and _prop(cs, "role") != "ca"
    return {
        "routers": _routers(topo, isp_names),
        "locations": [
            {"name": loc.id, "latitude": _prop(loc, "latitude"), "longitude": _prop(loc, "longitude")}
            for loc in topo.of("Location")
        ],
        "router_locations": _router_locations(topo, isp_names),
        "os_images": [
            {"name": img.id, "url": _prop(img, "url"), "type": _prop(img, "type")} for img in topo.of("Image")
        ],
        "core_dns": _attached(topo, "CoreService", "HOSTED_BY", not_ca, isp_names, _core_columns("zones")),
        "core_ca": _attached(topo, "CoreService", "HOSTED_BY", is_ca, isp_names, _core_columns("ca_name")),
        "links": _links(topo, isp_names),
    }
