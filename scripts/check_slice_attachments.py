#!/usr/bin/env python3
"""Refuse base changes that would break live slice instances.

task apply and task destroy run this, holding the node lock, before Terraform touches the base:

    check_slice_attachments.py apply     # after task parse wrote the new tfvars
    check_slice_attachments.py destroy

- destroy is refused while any instance exists.
- apply is refused if a router a live instance uses would lose its customer network or change
  its vnet, or if a base address would collide with an address an instance holds: a static
  address on a customer network, which must stay below the DHCP start, or a routed site's prefix.

Vnet names are positional (C2): a base edit can rename the vnet a slice is attached to, and
Terraform would replace the network under it. This is where that is caught. Destroy the affected
instances first (task slice:down, or DELETE /api/v1/instances/{id}).

Reads only JSON files, so it needs nothing beyond the Python standard library.
"""
import argparse
import ipaddress
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_instances(data_dir: Path):
    out = []
    for path in sorted((data_dir / "instances").glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit(f"❌ Can't read {path}: {e}")
        if record.get("state") != "destroyed":
            out.append(record)
    return out


def customer_bridge(router: dict):
    """As generate_tfvars.py assigns it: the router's interface on its customer network."""
    net = router.get("customer_net")
    if not net:
        return None
    network = ipaddress.IPv4Network(net)
    for iface in router.get("interfaces") or []:
        if iface.get("ospf_area") is None and iface.get("cidr") and \
                ipaddress.IPv4Network(f"{iface['ip']}/{iface['cidr']}", strict=False) == network:
            return iface["bridge"]
    return None


def base_addresses(tfvars: dict):
    """{address: owner} for every address the base assigns to a core service."""
    out = {}
    for key in ("core_dns", "core_ca"):
        for hostname, entry in (tfvars.get(key) or {}).items():
            ip = (entry.get("ip_address") or "").split("/")[0]
            if ip:
                out[ip] = hostname
    return out


def base_networks(tfvars: dict):
    nets = []
    for net in (tfvars.get("networks") or {}).values():
        if net.get("subnet"):
            nets.append(ipaddress.IPv4Network(net["subnet"], strict=False))
    for r in (tfvars.get("routers") or {}).values():
        for key in ("loopback_ip", "customer_net"):
            if r.get(key):
                nets.append(ipaddress.IPv4Network(r[key], strict=False))
    return nets


def attachments(record: dict):
    """(router, customer_net, bridge, what) for everything the instance joined on the base."""
    alloc = record.get("allocation") or {}
    for site in alloc.get("sites") or []:
        for wan in site.get("wans") or []:
            yield wan["router"], wan["customer_net"], wan["bridge"], f"site {site['name']}"
    for host in alloc.get("hosts") or []:
        if host.get("site") is None and host.get("router"):
            yield host["router"], None, host["bridge"], f"internet host {host['name']}"


def check_apply(tfvars: dict, instances: list) -> list:
    problems = []
    routers = tfvars.get("routers") or {}
    addresses = base_addresses(tfvars)
    networks = base_networks(tfvars)
    dhcp_start = int(tfvars.get("customer_dhcp_start") or 20)
    for record in instances:
        rid = record["id"]
        alloc = record.get("allocation") or {}
        for router, net, bridge, what in attachments(record):
            r = routers.get(router)
            if r is None:
                problems.append(f"instance {rid}: {what} is attached to {router}, which the new core doesn't have")
                continue
            if not r.get("customer_net"):
                problems.append(f"instance {rid}: {router} would lose the customer network {what} is attached to")
                continue
            if net and r["customer_net"] != net:
                problems.append(f"instance {rid}: {router}'s customer network would change from {net} to {r['customer_net']} under {what}")
            new_bridge = customer_bridge(r)
            if new_bridge != bridge:
                problems.append(f"instance {rid}: {router}'s customer network would move from vnet {bridge} to {new_bridge} under {what}")
        for static in alloc.get("static_addresses") or []:
            owner = addresses.get(static["ip"])
            if owner:
                problems.append(f"instance {rid}: the core would give {static['ip']} on {static['router']} to {owner}; {static['use']} holds it")
            r = routers.get(static["router"])
            if r and r.get("customer_net"):
                net = ipaddress.IPv4Network(r["customer_net"])
                offset = int(ipaddress.IPv4Address(static["ip"])) - int(net.network_address)
                if offset >= dhcp_start:
                    problems.append(f"instance {rid}: {static['ip']} ({static['use']}) would fall in {static['router']}'s DHCP range")
        for prefix in alloc.get("public_prefixes") or []:
            p = ipaddress.IPv4Network(prefix)
            clash = next((n for n in networks if n.overlaps(p)), None)
            if clash:
                problems.append(f"instance {rid}: the core would use {clash}, inside the routed site prefix {p}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=["apply", "destroy"])
    parser.add_argument("--tfvars", default=str(ROOT / "terraform" / "terraform.tfvars.json"))
    parser.add_argument("--data-dir", default=os.environ.get("GRAYSPACE_DATA_PATH") or str(ROOT / "terraform" / "grayspace-data"))
    args = parser.parse_args(argv)

    instances = load_instances(Path(args.data_dir))
    if not instances:
        print("✅ No live slice instances")
        return 0
    names = ", ".join(f"{r['id']} ({r.get('state')})" for r in instances)
    if args.mode == "destroy":
        print(f"❌ Refusing to destroy the core: live slice instances {names}. Destroy them first: task slice:down SLICE=... or DELETE /api/v1/instances/<id>")
        return 1
    try:
        tfvars = json.loads(Path(args.tfvars).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ Can't read {args.tfvars}: {e}")
        return 1
    problems = check_apply(tfvars, instances)
    if problems:
        print(f"❌ Refusing to apply: the new core would break live slice instances ({names})")
        for p in problems:
            print(f"   - {p}")
        print("   Destroy the affected instances first, or keep the core unchanged.")
        return 1
    print(f"✅ The new core keeps every attachment of the live slice instances: {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
