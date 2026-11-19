#!/usr/bin/env python3
# Script to delete LXC containers and associated SDN resources based on a tag filter.
# Uses Proxmoxer to interact with the Proxmox VE API.
import argparse
import re
import getpass

from proxmoxer import ProxmoxAPI

parser = argparse.ArgumentParser()
parser.add_argument("--username", help="Username for login (e.g., admin@pam)")
parser.add_argument("--password", help="Password for login (not recommended on CLI)")
parser.add_argument(
    "--node", help="Proxmox node name", required=True
)
parser.add_argument("--filter-tag", help="LXC tag to filter on", default="null")
parser.add_argument("--insecure", action="store_true", help="Disable SSL verification")

args = parser.parse_args()

if not args.username:
    args.username = input("Enter your username: ")

if not args.password:
    args.password = getpass.getpass("Enter your password: ")

proxmox = ProxmoxAPI(
    args.node,
    user=args.username,
    password=args.password,
    port=443,
    verify_ssl=not args.insecure,
)
for node in proxmox.nodes.get():
    for lxc in proxmox.nodes(node["node"]).lxc.get():
        try:
            if args.filter_tag in re.split(r"[;, ]+", lxc["tags"]):
                print(f"{lxc['vmid']} {lxc['name']} => {lxc['status']}")
                if lxc["status"] == "running":
                    print(f"Shutting down LXC {lxc['vmid']} {lxc['name']}")
                    shutdown = (
                        proxmox.nodes(node["node"])
                        .lxc(lxc["vmid"])
                        .status.shutdown.post()
                    )
        except KeyError:
            pass
# TODO: Add wait loop to ensure all containers are stopped before deletion
for node in proxmox.nodes.get():
    for lxc in proxmox.nodes(node["node"]).lxc.get():
        try:
            if args.filter_tag in re.split(r"[;, ]+", lxc["tags"]):
                if lxc["status"] == "stopped":
                    print(f"Deleting LXC {lxc['vmid']} {lxc['name']}")
                    proxmox.nodes(node["node"]).lxc(lxc["vmid"]).delete()
        except KeyError:
            pass
for vnet in proxmox.cluster.sdn.vnets.get():
    try:
        if args.filter_tag in vnet["zone"]:
            print(f"Deleting vNet {vnet['vnet']} from zone {vnet['zone']}")
            proxmox.cluster.sdn.vnets(vnet["vnet"]).delete()
    except KeyError:
        pass
# TODO: Check if SDN changes are pending and apply them
proxmox.cluster.sdn.put()

for zone in proxmox.cluster.sdn.zones.get():
    try:
        if args.filter_tag in zone["zone"]:
            print(f"Deleting zone {zone['zone']}")
            proxmox.cluster.sdn.zones(zone["zone"]).delete()
    except KeyError:
        pass

proxmox.cluster.sdn.put()
