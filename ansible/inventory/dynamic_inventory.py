#!/usr/bin/env python3
"""
This script generates an Ansible dynamic inventory by combining router data
from a JSON file with live IP address information from a Proxmox host.
"""
import glob
import json
import os

# Source of Truth for Topology
VARS_FILE = "../terraform/terraform.tfvars.json"

# Slice instances (grayspace-web's data directory): routed sites add BGP neighbors to the base
# routers they attach to.
DATA_DIR = os.environ.get("GRAYSPACE_DATA_PATH", "../terraform/grayspace-data")


def load_vars_from_json():
    """Loads the router and network data directly from the JSON file."""
    try:
        with open(VARS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Error: The vars file '{VARS_FILE}' was not found."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Error: Could not decode JSON from '{VARS_FILE}'.") from exc


def slice_attachments():
    """{router: [bgp neighbor]} from every live instance's attachments. Merging them into the
    routers' hostvars means task configure renders them into frr.conf like
    any customer, instead of erasing them."""
    out = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "instances", "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("state") == "destroyed":
            continue
        for a in record.get("attachments") or []:
            out.setdefault(a["router"], []).append({
                "hostname": a["edge"],
                "ip": a["ip"],
                "asn": a["asn"],
                "type": "PROVIDES_TRANSIT_TO",
                "relationship": "customer",
            })
    return out


ROLE_TO_GROUP = {
    "root-dns": "dns_root",
    "tld": "dns_tld",
    "hosting": "dns_hosting",
    "resolver": "dns_resolvers",
}


def main():
    lab_vars = load_vars_from_json()
    all_routers = lab_vars.get("routers", {})
    all_core_dns = lab_vars.get("core_dns", {})
    all_core_ca = lab_vars.get("core_ca", {})

    inventory = {
        "_meta": {"hostvars": {}},
        "all": {
            "children": ["routers", "core_dns", "core_ca"],
            # First DHCP lease offset on customer networks; the slice allocator reads it too
            "vars": {"customer_dhcp_start": lab_vars.get("customer_dhcp_start", 20)},
        },
        "routers": {"hosts": []},
        "core_dns": {
            "hosts": [],
            "children": ["dns_root", "dns_tld", "dns_hosting", "dns_resolvers"],
        },
        "dns_root": {"hosts": []},
        "dns_tld": {"hosts": []},
        "dns_hosting": {"hosts": []},
        "dns_resolvers": {"hosts": []},
        "core_ca": {"hosts": []},
    }

    # Populate routers
    attached = slice_attachments()
    for hostname, data in all_routers.items():
        inventory["routers"]["hosts"].append(hostname)
        neighbors = list(data.get("bgp_neighbors") or []) + attached.get(hostname, [])
        inventory["_meta"]["hostvars"][hostname] = {
            "ansible_user": "root",
            **data,
            "bgp_neighbors": neighbors,
        }

    # Populate core DNS
    for hostname, data in all_core_dns.items():
        inventory["core_dns"]["hosts"].append(hostname)
        role = data.get("role", "")
        group = ROLE_TO_GROUP.get(role)
        if group:
            inventory[group]["hosts"].append(hostname)
        inventory["_meta"]["hostvars"][hostname] = {
            "ansible_user": "root",
            **data,
        }

    # Populate core CA
    for hostname, data in all_core_ca.items():
        inventory["core_ca"]["hosts"].append(hostname)
        inventory["_meta"]["hostvars"][hostname] = {
            "ansible_user": "root",
            **data,
        }

    print(json.dumps(inventory, indent=4))


if __name__ == "__main__":
    main()
