#!/usr/bin/env python3
"""Simulated router metrics for local development, in place of frr_exporter and node_exporter.

Serves, for every router in a tfvars file, the series grayspace-web reads from Prometheus:

    /metrics/frr    frr_bgp_peer_state, frr_ospf_neighbor_state       (job="frr")
    /metrics/node   node_network_up and the traffic counters           (job="node")

Each series carries instance="<router hostname>"; Prometheus scrapes with honor_labels so the
label is kept, as relabelling does in a deployment (ansible/templates/prometheus.yml.j2).

Everything is up unless taken down, here or by stopping a router in the web UI:

    POST /links/<r1:r2>/down | /up     both ends' interfaces, the OSPF adjacency and eBGP session
    POST /routers/<hostname>/down | /up    the router stops reporting; its neighbors see it down
    POST /reset                         everything up again
    GET  /                              what is down, as JSON

STATE_FILE (JSON list of stopped hostnames), written by grayspace-web's simulated deployment
(DEV_SIMULATE_DEPLOYMENT), also takes routers down.

    TFVARS=terraform/terraform.tfvars.json PORT=9342 python3 router_metrics.py
"""
import hashlib
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def links(routers: dict) -> dict:
    """Router-to-router links keyed r1:r2, as grayspace-web names them: two routers on one vnet,
    in tfvars order (services/slices/base.py links())."""
    by_bridge = {}
    for hostname, r in routers.items():
        for idx, iface in enumerate(r.get("interfaces") or []):
            if iface.get("bridge"):
                by_bridge.setdefault(iface["bridge"], []).append((hostname, idx, iface))
    out = {}
    for ends in by_bridge.values():
        if len(ends) != 2:
            continue
        (h1, i1, f1), (h2, i2, f2) = ends
        if f"{h2}:{h1}" in out or f"{h1}:{h2}" in out:
            continue
        out[f"{h1}:{h2}"] = {"ends": [(h1, i1, f1), (h2, i2, f2)], "ospf_area": f1.get("ospf_area")}
    return out


def _address(value):
    return (value or "").split("/")[0]


class Network:
    def __init__(self, tfvars: dict, state_file=None):
        self.routers = tfvars.get("routers") or {}
        self.links = links(self.routers)
        self.state_file = Path(state_file) if state_file else None
        self.links_down = set()
        self.routers_down = set()
        self.lock = threading.Lock()
        self.counters = {}
        self.last = time.monotonic()
        # Which link each router interface is on, and which router owns each address
        self.iface_link = {}
        for link_id, link in self.links.items():
            for hostname, idx, _ in link["ends"]:
                self.iface_link[(hostname, idx)] = link_id
        self.owner = {}
        for hostname, r in self.routers.items():
            self.owner[_address(r.get("loopback_ip"))] = hostname
            for iface in r.get("interfaces") or []:
                self.owner[_address(iface.get("ip"))] = hostname

    # --- state ---

    def canonical(self, link_id: str):
        if link_id in self.links:
            return link_id
        a, _, b = link_id.partition(":")
        return f"{b}:{a}" if f"{b}:{a}" in self.links else None

    def stopped(self) -> set:
        if not self.state_file or not self.state_file.exists():
            return set()
        try:
            return set(json.loads(self.state_file.read_text()))
        except (OSError, ValueError):
            return set()

    def down_routers(self) -> set:
        return self.routers_down | (self.stopped() & set(self.routers))

    def link_up(self, link_id: str, down_routers: set) -> bool:
        link = self.links[link_id]
        return link_id not in self.links_down and not any(h in down_routers for h, _, _ in link["ends"])

    def status(self) -> dict:
        with self.lock:
            down = self.down_routers()
            return {
                "routers": len(self.routers),
                "links": len(self.links),
                "links_down": sorted(self.links_down),
                "routers_down": sorted(down),
                "routers_stopped_in_ui": sorted(self.stopped() & set(self.routers)),
            }

    # --- metrics ---

    def frr(self) -> str:
        with self.lock:
            down = self.down_routers()
            lines = [
                "# HELP frr_bgp_peer_state State of the BGP peer (0 = down, 1 = up, 2 = shutdown).",
                "# TYPE frr_bgp_peer_state gauge",
            ]
            for hostname, r in self.routers.items():
                if hostname in down:
                    continue
                for n in r.get("bgp_neighbors") or []:
                    peer = n.get("hostname") or self.owner.get(n["ip"])
                    up = peer not in down
                    if up and n.get("type") != "INTERNAL_LINK":
                        # An eBGP session runs over the link between the two routers.
                        link_id = self.canonical(f"{hostname}:{peer}")
                        up = link_id is None or self.link_up(link_id, down)
                    lines.append(
                        f'frr_bgp_peer_state{{instance="{hostname}",peer="{n["ip"]}",peer_as="{n.get("asn")}",'
                        f'vrf="default",afi="ipv4",safi="unicast"}} {1 if up else 0}'
                    )
            lines += [
                "# HELP frr_ospf_neighbor_state State of the OSPF neighbor (7 = full).",
                "# TYPE frr_ospf_neighbor_state gauge",
            ]
            for link_id, link in self.links.items():
                if link["ospf_area"] is None:
                    continue
                up = self.link_up(link_id, down)
                for (h, idx, _), (peer, _, peer_iface) in (link["ends"], link["ends"][::-1]):
                    if h in down:
                        continue
                    lines.append(
                        f'frr_ospf_neighbor_state{{instance="{h}",iface="eth{idx}",area="{link["ospf_area"]}",'
                        f'neighbor="{_address(peer_iface["ip"])}",id="{_address(self.routers[peer].get("loopback_ip"))}"}} {7 if up else 0}'
                    )
            return "\n".join(lines) + "\n"

    def _rate(self, hostname, device) -> float:
        """A steady, made-up rate in bytes/s for an interface (1-80 Mbps)."""
        h = int(hashlib.sha256(f"{hostname}/{device}".encode()).hexdigest()[:8], 16)
        return (1 + h % 80) * 125_000

    def node(self) -> str:
        with self.lock:
            down = self.down_routers()
            now = time.monotonic()
            dt, self.last = now - self.last, now
            series = {name: [] for name in (
                "node_network_up", "node_network_speed_bytes",
                "node_network_receive_bytes_total", "node_network_transmit_bytes_total",
                "node_network_receive_packets_total", "node_network_transmit_packets_total",
                "node_network_receive_errs_total", "node_network_transmit_errs_total",
                "node_network_receive_drop_total", "node_network_transmit_drop_total",
            )}
            for hostname, r in self.routers.items():
                if hostname in down:
                    continue
                interfaces = r.get("interfaces") or []
                devices = [(f"eth{i}", (hostname, i)) for i in range(len(interfaces))] + [(f"eth{len(interfaces)}", None), ("lo", None)]
                for device, key in devices:
                    link_id = self.iface_link.get(key) if key else None
                    up = link_id is None or self.link_up(link_id, down)
                    counter = self.counters.setdefault((hostname, device), [0.0, 0.0])
                    if up:
                        rate = self._rate(hostname, device)
                        counter[0] += rate * dt
                        counter[1] += rate * 0.8 * dt
                    labels = f'instance="{hostname}",device="{device}"'
                    series["node_network_up"].append(f"node_network_up{{{labels}}} {1 if up else 0}")
                    series["node_network_speed_bytes"].append(f"node_network_speed_bytes{{{labels}}} 1250000000")
                    series["node_network_receive_bytes_total"].append(f"node_network_receive_bytes_total{{{labels}}} {counter[0]:.0f}")
                    series["node_network_transmit_bytes_total"].append(f"node_network_transmit_bytes_total{{{labels}}} {counter[1]:.0f}")
                    series["node_network_receive_packets_total"].append(f"node_network_receive_packets_total{{{labels}}} {counter[0] / 800:.0f}")
                    series["node_network_transmit_packets_total"].append(f"node_network_transmit_packets_total{{{labels}}} {counter[1] / 800:.0f}")
                    for name in ("receive_errs", "transmit_errs", "receive_drop", "transmit_drop"):
                        series[f"node_network_{name}_total"].append(f"node_network_{name}_total{{{labels}}} 0")
            lines = []
            for name, values in series.items():
                kind = "gauge" if name in ("node_network_up", "node_network_speed_bytes") else "counter"
                lines += [f"# TYPE {name} {kind}", *values]
            return "\n".join(lines) + "\n"


def handler_for(network: Network):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, content_type="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/metrics/frr":
                self._send(200, network.frr(), "text/plain; version=0.0.4")
            elif self.path == "/metrics/node":
                self._send(200, network.node(), "text/plain; version=0.0.4")
            elif self.path in ("/", "/status"):
                self._send(200, network.status())
            else:
                self._send(404, {"detail": "not found"})

        def do_POST(self):
            match = re.fullmatch(r"/(links|routers)/([^/]+)/(down|up)", self.path)
            if self.path == "/reset":
                with network.lock:
                    network.links_down.clear()
                    network.routers_down.clear()
                return self._send(200, network.status())
            if not match:
                return self._send(404, {"detail": "not found"})
            kind, target, action = match.groups()
            with network.lock:
                if kind == "links":
                    link_id = network.canonical(target)
                    if link_id is None:
                        return self._send(404, {"detail": f"no link {target}"})
                    (network.links_down.add if action == "down" else network.links_down.discard)(link_id)
                else:
                    if target not in network.routers:
                        return self._send(404, {"detail": f"no router {target}"})
                    (network.routers_down.add if action == "down" else network.routers_down.discard)(target)
            self._send(200, network.status())

        def log_message(self, fmt, *args):
            if os.environ.get("SIM_QUIET") != "1":
                super().log_message(fmt, *args)

    return Handler


def serve(tfvars_path: str, port: int, state_file=None, host="0.0.0.0") -> ThreadingHTTPServer:
    network = Network(json.loads(Path(tfvars_path).read_text()), state_file)
    server = ThreadingHTTPServer((host, port), handler_for(network))
    server.network = network
    return server


if __name__ == "__main__":
    server = serve(os.environ["TFVARS"], int(os.environ.get("PORT", "9342")), os.environ.get("STATE_FILE"))
    n = server.network
    print(f"Simulating {len(n.routers)} routers and {len(n.links)} links on port {server.server_address[1]}", flush=True)
    server.serve_forever()
