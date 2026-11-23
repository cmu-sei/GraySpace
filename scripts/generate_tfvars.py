#!/usr/bin/env python3

import argparse
import ipaddress
import json
import math
from pathlib import Path

import topology

SCOPES = ["full", "tier1"]

# The shipped base version used when neither --base nor --csv-dir is given.
DEFAULT_BASE = "grayspace/2026.11"

# dnsmasq on each router leases customer network addresses from this host offset up (.20). The
# addresses below it (.2-.19) are static: core services, and slice edges and
# internet hosts. Written into the tfvars so the dnsmasq template
# and the slice allocator read the same value.
CUSTOMER_DHCP_START = 20

# GraySpace's root: the directory holding scripts/, bases/ and terraform/. Found from this file,
# not the git root, so it stays correct when GraySpace is a subdirectory of another repository.
ROOT = Path(__file__).resolve().parent.parent


def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points on Earth.
    Returns distance in kilometers.
    """
    # Earth's radius in kilometers
    R = 6371.0

    # Convert degrees to radians
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    # Haversine formula
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))

    distance = R * c
    return distance


def calculate_latency(distance_km):
    """
    Calculate network latency based on physical distance.

    Light travels at ~200,000 km/s in fiber optic cable (2/3 speed of light in vacuum).
    This gives us ~5 microseconds per kilometer, or 0.005 ms per km.

    Returns latency in milliseconds, rounded to 2 decimal places.
    """
    # Speed of light in fiber: ~200,000 km/s
    # Time per km: 1/200,000 seconds = 0.000005 seconds = 0.005 ms
    latency_ms = distance_km * 0.005
    return round(latency_ms, 2)


def resolve_image(image_name, image_map):
    """Resolve an image name to its URL using the image map.
    If the name exists in the map, return the URL; otherwise return as-is."""
    return image_map[image_name]["url"] if image_name in image_map else image_name


# Helper function to safely add iBGP peerings ---
def add_ibgp_peering(routers_dict, source_host, peer_host):
    """
    Adds a logical iBGP peering using loopback addresses,
    avoiding duplicates if a physical peering already exists.
    """
    # Check if this peering already exists from a physical link
    existing_peer_hostnames = [
        p["hostname"] for p in routers_dict[source_host]["bgp_neighbors"]
    ]
    if peer_host not in existing_peer_hostnames:
        peer_data = routers_dict[peer_host]
        # iBGP peerings are always between loopback IPs
        peer_loopback = peer_data["loopback_ip"].split("/")[0]

        routers_dict[source_host]["bgp_neighbors"].append(
            {
                "hostname": peer_host,
                "ip": peer_loopback,
                "asn": peer_data["asn"],
                "type": "INTERNAL_LINK",
            }
        )


def main():

    # Command-Line Argument Parsing
    parser = argparse.ArgumentParser(
        description="Generate Terraform variables for Proxmox networking from the topology CSVs."
    )
    parser.add_argument(
        "--scope",
        required=True,
        choices=SCOPES,
        help="Define the scope of the deployment: the whole model, or only the Tier1 ISPs.",
    )
    parser.add_argument(
        "--output-file",
        default=ROOT / "terraform" / "terraform.tfvars.json",
        help="Optional path to the output JSON file for Terraform. Defaults to terraform/terraform.tfvars.json",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--base",
        default=DEFAULT_BASE,
        help=f"Shipped core version to read from bases/. Defaults to {DEFAULT_BASE}",
    )
    source.add_argument(
        "--csv-dir",
        help="Read the topology CSVs from this directory instead of a shipped core version.",
    )
    args = parser.parse_args()
    csv_dir = Path(args.csv_dir) if args.csv_dir else ROOT / "bases" / args.base
    if not csv_dir.is_dir():
        shipped = sorted(str(p.relative_to(ROOT / "bases")) for p in (ROOT / "bases").glob("*/*") if p.is_dir())
        print(f"❌ {csv_dir} does not exist. Shipped core versions: {', '.join(shipped) or 'none'}")
        raise SystemExit(1)

    print(f"Running with deployment scope: '{args.scope}', topology: {csv_dir}")

    try:
        query_results = topology.query_rows(topology.load(csv_dir), args.scope)
    except topology.LoadError as e:
        print(f"❌ {csv_dir} is not a valid topology:\n{e}")
        raise SystemExit(1)

    routers_data = query_results["routers"]
    core_dns_data = query_results["core_dns"]
    core_ca_data = query_results["core_ca"]
    links_data = query_results["links"]
    locations_data = query_results["locations"]
    router_locations_data = query_results["router_locations"]

    # Build image map from os_images query
    image_map = {
        img["name"]: {"url": img["url"], "type": img["type"]}
        for img in query_results["os_images"]
    }

    # Initialize Dictionaries
    ansible_routers = {r["hostname"]: r for r in routers_data}

    # Build core DNS dict and resolve images
    ansible_core_dns = {cs["hostname"]: cs for cs in core_dns_data}
    for hostname in ansible_core_dns:
        ansible_core_dns[hostname]["image"] = resolve_image(
            ansible_core_dns[hostname]["image"], image_map
        )
        if ansible_core_dns[hostname]["zones"] is None:
            ansible_core_dns[hostname]["zones"] = []

    # Build core CA dict and resolve images
    ansible_core_ca = {cs["hostname"]: cs for cs in core_ca_data}
    for hostname in ansible_core_ca:
        ansible_core_ca[hostname]["image"] = resolve_image(
            ansible_core_ca[hostname]["image"], image_map
        )

    terraform_networks = {}

    # Build location mappings
    location_map = {
        loc["name"]: {
            "latitude": loc["latitude"],
            "longitude": loc["longitude"]
        }
        for loc in locations_data
    }
    router_location_map = {
        rl["hostname"]: rl["location_name"]
        for rl in router_locations_data
    }

    # Build location mappings
    location_map = {
        loc["name"]: {
            "latitude": loc["latitude"],
            "longitude": loc["longitude"]
        }
        for loc in locations_data
    }
    router_location_map = {
        rl["hostname"]: rl["location_name"]
        for rl in router_locations_data
    }

    # Initialize router interfaces and BGP neighbors
    for hostname in ansible_routers:
        ansible_routers[hostname]["interfaces"] = []
        ansible_routers[hostname]["bgp_neighbors"] = []

        # Add location data to router
        location_name = router_location_map.get(hostname)
        if location_name and location_name in location_map:
            ansible_routers[hostname]["location"] = {
                "name": location_name,
                "latitude": location_map[location_name]["latitude"],
                "longitude": location_map[location_name]["longitude"]
            }
        else:
            ansible_routers[hostname]["location"] = None

    # Initialize service interfaces
    for service_dict in [ansible_core_dns, ansible_core_ca]:
        for hostname in service_dict:
            service_dict[hostname]["interfaces"] = []

    # simple router for simple bridge name assignment
    bridge_counter = 100

    # Process Point-to-Point Links
    for link in links_data:
        r1_hostname = link["r1_hostname"]
        r2_hostname = link["r2_hostname"]
        bridge_name = f"v{bridge_counter}"
        bridge_desc = f"p2p link between {r1_hostname} and {r2_hostname}"

        # Calculate distance and latency based on geographic coordinates
        distance_km = haversine_distance(
            link["r1_lat"], link["r1_lon"], link["r2_lat"], link["r2_lon"]
        )
        latency_ms = calculate_latency(distance_km) / 2

        terraform_networks[bridge_name] = {
            "subnet": link["subnet"],
            "description": bridge_desc,
            "distance_km": round(distance_km, 2),
            "latency_ms": latency_ms,
        }

        bridge_counter += 1

        # Extract CIDR from subnet string
        cidr = link["subnet"].split("/")[1] if link["subnet"] else None

        if r1_hostname in ansible_routers and r2_hostname in ansible_routers:
            # Add interfaces with CIDR and latency
            ansible_routers[r1_hostname]["interfaces"].append(
                {
                    "bridge": bridge_name,
                    "ip": link["r1_ip"],
                    "cidr": cidr,
                    "ospf_area": link["ospf_area"],
                    "latency_ms": latency_ms,
                }
            )
            ansible_routers[r2_hostname]["interfaces"].append(
                {
                    "bridge": bridge_name,
                    "ip": link["r2_ip"],
                    "cidr": cidr,
                    "ospf_area": link["ospf_area"],
                    "latency_ms": latency_ms,
                }
            )

        if link["link_type"] in ["PEERS_WITH", "PROVIDES_TRANSIT_TO"]:
            # Relationship is the neighbor's role relative to this router.
            # For PROVIDES_TRANSIT_TO, r1 is the provider and r2 the customer.
            if link["link_type"] == "PROVIDES_TRANSIT_TO":
                r1_sees_r2, r2_sees_r1 = "customer", "provider"
            else:
                r1_sees_r2, r2_sees_r1 = "peer", "peer"

            ansible_routers[r1_hostname]["bgp_neighbors"].append(
                {
                    "hostname": r2_hostname,
                    "ip": link["r2_ip"],
                    "asn": ansible_routers[r2_hostname]["asn"],
                    "type": link["link_type"],
                    "relationship": r1_sees_r2,
                }
            )
            ansible_routers[r2_hostname]["bgp_neighbors"].append(
                {
                    "hostname": r1_hostname,
                    "ip": link["r1_ip"],
                    "asn": ansible_routers[r1_hostname]["asn"],
                    "type": link["link_type"],
                    "relationship": r2_sees_r1,
                }
            )

    # Build Route Reflector iBGP Topology
    # Group routers by ASN
    as_map = {}
    for hostname, data in ansible_routers.items():
        asn = data["asn"]
        if asn not in as_map:
            as_map[asn] = []
        as_map[asn].append(hostname)

    # For each ASN, identify RRs and Clients, then build peerings
    for _, hostnames in as_map.items():
        route_reflectors = []
        clients = []
        for hostname in hostnames:
            roles_list = []
            if ansible_routers[hostname].get("roles"):
                roles_list = ansible_routers[hostname]["roles"][0].split(",")

            if "RR" in roles_list:
                route_reflectors.append(hostname)
            else:
                clients.append(hostname)

        if not route_reflectors:
            # No RR — build full-mesh iBGP between all routers in this ASN
            for i, hostname_i in enumerate(hostnames):
                for hostname_j in hostnames[i + 1:]:
                    add_ibgp_peering(ansible_routers, hostname_i, hostname_j)
                    add_ibgp_peering(ansible_routers, hostname_j, hostname_i)
            continue

        # Build peerings
        # Full mesh between all Route Reflectors
        for rr_host in route_reflectors:
            for other_rr_host in route_reflectors:
                if rr_host != other_rr_host:
                    add_ibgp_peering(ansible_routers, rr_host, other_rr_host)

        # RRs to all their clients
        for rr_host in route_reflectors:
            for client_host in clients:
                add_ibgp_peering(ansible_routers, rr_host, client_host)

        # Clients back to all RRs
        for client_host in clients:
            for rr_host in route_reflectors:
                add_ibgp_peering(ansible_routers, client_host, rr_host)

    # Process Customer Network Interfaces
    router_customer_bridge = {}
    for r_data in routers_data:
        hostname = r_data["hostname"]
        customer_net_str = r_data.get("customer_net")
        bridge_desc = f"Customer network for {hostname}"

        if hostname and customer_net_str:
            try:
                bridge_name = f"v{bridge_counter}"
                terraform_networks[bridge_name] = {
                    "subnet": customer_net_str,
                    "description": bridge_desc,
                }

                bridge_counter += 1

                network = ipaddress.ip_network(customer_net_str)
                router_ip = str(next(network.hosts()))
                cidr = str(network.prefixlen)

                ansible_routers[hostname]["interfaces"].append(
                    {
                        "bridge": bridge_name,
                        "ip": router_ip,
                        "cidr": cidr,
                        "ospf_area": None,
                    }
                )
                router_customer_bridge[hostname] = bridge_name
            except (ValueError, TypeError) as e:
                print(
                    f"⚠️  Skipping invalid customer network '{customer_net_str}' for router '{hostname}': {e}"
                )

    # Process Core Service Network Interfaces
    # Core services use HOSTED_BY subnets — create bridges and link to routers
    for cs_data in core_dns_data:
        hostname = cs_data["hostname"]
        router = cs_data.get("router")
        subnet = cs_data.get("subnet")
        ip_address = cs_data.get("ip_address")

        if hostname and subnet and router:
            try:
                # If the service subnet matches the router's customer network,
                # reuse that bridge instead of creating a duplicate interface
                customer_net = ansible_routers.get(router, {}).get("customer_net")

                if customer_net and customer_net == subnet and router in router_customer_bridge:
                    bridge_name = router_customer_bridge[router]
                else:
                    bridge_name = f"v{bridge_counter}"
                    terraform_networks[bridge_name] = {
                        "subnet": subnet,
                        "description": f"Core service network for {hostname} on {router}",
                    }
                    bridge_counter += 1

                    # Add interface to the hosting router only for new bridges
                    if router in ansible_routers:
                        network = ipaddress.ip_network(subnet, strict=False)
                        router_ip = str(next(network.hosts()))
                        cidr = str(network.prefixlen)
                        ansible_routers[router]["interfaces"].append(
                            {
                                "bridge": bridge_name,
                                "ip": router_ip,
                                "cidr": cidr,
                                "ospf_area": None,
                            }
                        )

                # Add interface to the core service
                if hostname in ansible_core_dns and ip_address:
                    ansible_core_dns[hostname]["interfaces"].append(
                        {
                            "bridge": bridge_name,
                            "ip": ip_address,
                        }
                    )
            except (ValueError, TypeError) as e:
                print(
                    f"⚠️  Skipping invalid core service network '{subnet}' for '{hostname}': {e}"
                )

    # Process Core CA Network Interfaces
    for cs_data in core_ca_data:
        hostname = cs_data["hostname"]
        router = cs_data.get("router")
        subnet = cs_data.get("subnet")
        ip_address = cs_data.get("ip_address")

        if hostname and subnet and router:
            try:
                customer_net = ansible_routers.get(router, {}).get("customer_net")

                if customer_net and customer_net == subnet and router in router_customer_bridge:
                    bridge_name = router_customer_bridge[router]
                else:
                    bridge_name = f"v{bridge_counter}"
                    terraform_networks[bridge_name] = {
                        "subnet": subnet,
                        "description": f"Core CA network for {hostname} on {router}",
                    }
                    bridge_counter += 1

                    if router in ansible_routers:
                        network = ipaddress.ip_network(subnet, strict=False)
                        router_ip = str(next(network.hosts()))
                        cidr = str(network.prefixlen)
                        ansible_routers[router]["interfaces"].append(
                            {
                                "bridge": bridge_name,
                                "ip": router_ip,
                                "cidr": cidr,
                                "ospf_area": None,
                            }
                        )

                if hostname in ansible_core_ca and ip_address:
                    ansible_core_ca[hostname]["interfaces"].append(
                        {
                            "bridge": bridge_name,
                            "ip": ip_address,
                        }
                    )
            except (ValueError, TypeError) as e:
                print(
                    f"⚠️  Skipping invalid core CA network '{subnet}' for '{hostname}': {e}"
                )

    # Create the final combined JSON object
    final_output = {
        "routers": ansible_routers,
        "core_dns": ansible_core_dns,
        "core_ca": ansible_core_ca,
        "networks": terraform_networks,
        "locations": location_map,
        "customer_dhcp_start": CUSTOMER_DHCP_START,
        # What this file was generated from, so the deployed base version can be reported
        # (GET /api/v1/base). name is null when the CSVs came from --csv-dir.
        "base": {
            "name": None if args.csv_dir else args.base,
            "csv_sha256": topology.csv_sha256(csv_dir),
            "scope": args.scope,
        },
    }

    with open(args.output_file, "w") as f:
        json.dump(final_output, f, indent=2)
    print(f"✅ Successfully created {args.output_file}")


if __name__ == "__main__":
    main()
