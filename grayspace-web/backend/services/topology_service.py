"""Topology service using hybrid Proxmox + tfvars approach."""
import ipaddress
import json
import math
import time
from collections import defaultdict
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from services.proxmox_client import proxmox_client
from config import settings
import logging

logger = logging.getLogger(__name__)


class TopologyService:
    """
    Hybrid topology service that combines Proxmox (source of truth for deployment)
    with terraform.tfvars.json (source of truth for metadata).

    Architecture:
    - Proxmox API tells us WHAT is deployed (container names)
    - tfvars.json tells us metadata ABOUT what's deployed (location, ISP, BGP neighbors)
    - Only data for actually deployed containers is returned
    """

    def __init__(self):
        self.tfvars_data: Dict[str, Any] = {}
        self.deployed_hostnames_cache: Optional[Set[str]] = None
        self.cache_timestamp: Optional[float] = None
        self.cache_ttl: int = 30  # seconds
        self._load_tfvars()

    def _load_tfvars(self):
        """Load terraform.tfvars.json from configured path."""
        tfvars_path = Path(settings.tfvars_path)
        try:
            with open(tfvars_path, 'r') as f:
                self.tfvars_data = json.load(f)
            logger.info(f"Loaded tfvars from {tfvars_path}")
            logger.info(f"  - Routers in tfvars: {len(self.tfvars_data.get('routers', {}))}")
            logger.info(f"  - Locations in tfvars: {len(self.tfvars_data.get('locations', {}))}")
        except FileNotFoundError:
            logger.error(f"tfvars file not found: {tfvars_path}")
            self.tfvars_data = {}
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing tfvars JSON: {e}")
            self.tfvars_data = {}

    @staticmethod
    def _normalize_csv_list(values) -> list:
        """Split comma-separated entries so ['ABR,RR'] becomes ['ABR','RR']."""
        if not values:
            return []
        result = []
        for v in values:
            result.extend(part.strip() for part in v.split(","))
        return result

    def get_deployed_hostnames(self) -> Set[str]:
        """
        Query Proxmox for deployed container hostnames (cached for 30s).

        CRITICAL: This is the source of truth for what's actually deployed.
        Only containers that exist in Proxmox will be shown in the web UI.

        Returns:
            Set of container hostnames that are actually deployed.
        """
        # Check cache
        if self.cache_timestamp and (time.time() - self.cache_timestamp < self.cache_ttl):
            if self.deployed_hostnames_cache is not None:
                return self.deployed_hostnames_cache

        # Query Proxmox
        containers = proxmox_client.get_deployment_containers()
        hostnames = {c["name"] for c in containers}

        if not hostnames and self.deployed_hostnames_cache:
            logger.warning("Proxmox returned empty container list — keeping previous cache")
            return self.deployed_hostnames_cache

        self.deployed_hostnames_cache = hostnames
        self.cache_timestamp = time.time()

        logger.info(f"Refreshed deployed hostnames cache: {len(self.deployed_hostnames_cache)} containers")
        return self.deployed_hostnames_cache

    def get_routers_with_locations(self) -> List[Dict[str, Any]]:
        """
        Get only deployed routers with location data.

        This method:
        1. Queries Proxmox for deployed container names
        2. Filters tfvars routers to only include deployed ones
        3. Returns router data enriched with location metadata
        4. Applies offset to routers at the same location to prevent overlap

        Returns:
            List of router dictionaries with location information.
        """
        deployed = self.get_deployed_hostnames()
        routers = []

        for hostname, router_data in self.tfvars_data.get("routers", {}).items():
            if hostname not in deployed:
                continue

            location = router_data.get("location")
            if not location:
                logger.warning(f"Router {hostname} is deployed but has no location data in tfvars")
                continue

            routers.append({
                "hostname": hostname,
                "loopback_ip": router_data.get("loopback_ip"),
                "roles": self._normalize_csv_list(router_data.get("roles", [])),
                "ospf_areas": router_data.get("ospf_areas", []) if router_data.get("ospf_areas") else [],
                "isp_name": router_data.get("isp_name"),
                "asn": router_data.get("asn"),
                "location": location,
                "customer_net": router_data.get("customer_net")
            })

        # Apply offset to routers sharing the same location
        routers = self._apply_location_offsets(routers)

        logger.info(f"Retrieved {len(routers)} deployed routers with locations")
        return routers

    def _apply_location_offsets(self, routers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Apply small coordinate offsets to routers at the same location.

        Routers at the same city are arranged in a circle around the original
        coordinates so they don't overlap on the map.

        Args:
            routers: List of router dictionaries with location data.

        Returns:
            List of routers with adjusted coordinates.
        """
        # Group routers by location name
        location_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for router in routers:
            loc = router.get("location")
            if loc and loc.get("name"):
                location_groups[loc["name"]].append(router)

        # Offset radius in degrees (approximately 0.3 degrees ≈ 30km)
        OFFSET_RADIUS = 0.3

        for location_name, group in location_groups.items():
            if len(group) <= 1:
                continue  # No offset needed for single router

            # Arrange routers in a circle around the original location
            num_routers = len(group)
            for i, router in enumerate(group):
                angle = (2 * math.pi * i) / num_routers
                lat_offset = OFFSET_RADIUS * math.cos(angle)
                lon_offset = OFFSET_RADIUS * math.sin(angle)

                # Create new location dict with offset coordinates
                original_loc = router["location"]
                router["location"] = {
                    "name": original_loc["name"],
                    "latitude": original_loc["latitude"] + lat_offset,
                    "longitude": original_loc["longitude"] + lon_offset
                }

        return routers

    def get_connections(self) -> List[Dict[str, Any]]:
        """
        Build physical connections from tfvars interfaces (routers sharing bridges).

        This method finds physical links by looking at which routers share the same
        network bridge. It excludes logical iBGP sessions (INTERNAL_LINK) which use
        loopback IPs and aren't physical connections.

        Only returns connections where BOTH routers are deployed in Proxmox.

        Returns:
            List of connection dictionaries representing physical links.
        """
        deployed = self.get_deployed_hostnames()
        connections = []
        seen_pairs = set()  # Track seen pairs to avoid duplicates

        # Build a map of bridge -> list of (hostname, interface_data)
        bridge_to_routers: Dict[str, List[tuple]] = defaultdict(list)

        for hostname, router_data in self.tfvars_data.get("routers", {}).items():
            if hostname not in deployed:
                continue

            for interface in router_data.get("interfaces", []):
                bridge = interface.get("bridge")
                if bridge:
                    bridge_to_routers[bridge].append((hostname, interface))

        # Find connections: routers that share the same bridge are connected
        for bridge, router_interfaces in bridge_to_routers.items():
            if len(router_interfaces) != 2:
                # Skip bridges with only one router (customer networks) or more than 2
                continue

            (hostname1, iface1), (hostname2, iface2) = router_interfaces

            # Avoid duplicate connections
            pair_key = tuple(sorted([hostname1, hostname2]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            # Determine connection type from bgp_neighbors if available
            conn_type = "CONNECTION"
            router1_data = self.tfvars_data.get("routers", {}).get(hostname1, {})
            for neighbor in router1_data.get("bgp_neighbors", []):
                if neighbor.get("hostname") == hostname2:
                    neighbor_type = neighbor.get("type", "CONNECTION")
                    # Skip INTERNAL_LINK - those are logical iBGP, not physical
                    if neighbor_type != "INTERNAL_LINK":
                        conn_type = neighbor_type
                    break

            # Calculate interface names (eth0, eth1, etc.) based on position in interfaces array
            source_interface = None
            target_interface = None
            router1_interfaces = router1_data.get("interfaces", [])
            router2_data = self.tfvars_data.get("routers", {}).get(hostname2, {})
            router2_interfaces = router2_data.get("interfaces", [])

            for idx, iface in enumerate(router1_interfaces):
                if iface.get("bridge") == bridge:
                    source_interface = f"eth{idx}"
                    break

            for idx, iface in enumerate(router2_interfaces):
                if iface.get("bridge") == bridge:
                    target_interface = f"eth{idx}"
                    break

            connections.append({
                "id": f"{hostname1}:{hostname2}",
                "source_hostname": hostname1,
                "target_hostname": hostname2,
                "type": conn_type,
                "source_ip": iface1.get("ip"),
                "target_ip": iface2.get("ip"),
                "subnet": str(ipaddress.ip_network(f"{iface1.get('ip')}/{iface1.get('cidr')}", strict=False)) if iface1.get("cidr") else None,
                "ospf_area": iface1.get("ospf_area"),
                "source_interface": source_interface,
                "target_interface": target_interface
            })

        logger.info(f"Retrieved {len(connections)} physical connections between deployed routers")
        return connections

    def get_isps(self) -> List[Dict[str, Any]]:
        """
        Extract unique ISPs from deployed routers only.

        Returns:
            List of ISP dictionaries.
        """
        deployed = self.get_deployed_hostnames()
        isps_dict = {}

        for hostname, router_data in self.tfvars_data.get("routers", {}).items():
            if hostname not in deployed:
                continue

            isp_name = router_data.get("isp_name")
            if not isp_name or isp_name in isps_dict:
                continue

            isps_dict[isp_name] = {
                "name": isp_name,
                "asn": router_data.get("asn"),
                "public_block": router_data.get("public_block")
            }

        isps = list(isps_dict.values())
        logger.info(f"Retrieved {len(isps)} ISPs from deployed routers")
        return isps

    def get_router_detail(self, hostname: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific router (if deployed in Proxmox).

        Args:
            hostname: Router hostname.

        Returns:
            Router detail dictionary or None if not found or not deployed.
        """
        # Check if deployed
        deployed = self.get_deployed_hostnames()
        if hostname not in deployed:
            logger.warning(f"Router {hostname} not found in Proxmox deployment")
            return None

        # Get data from tfvars
        router_data = self.tfvars_data.get("routers", {}).get(hostname)
        if not router_data:
            logger.warning(f"Router {hostname} is deployed but not found in tfvars")
            return None

        # Build neighbors list (only include deployed neighbors)
        neighbors = []
        for neighbor in router_data.get("bgp_neighbors", []):
            neighbor_hostname = neighbor.get("hostname")
            if neighbor_hostname in deployed:
                neighbors.append({
                    "hostname": neighbor_hostname,
                    "ip": neighbor.get("ip"),
                    "type": neighbor.get("type", "UNKNOWN"),
                    "asn": neighbor.get("asn")
                })

        return {
            "hostname": hostname,
            "loopback_ip": router_data.get("loopback_ip"),
            "roles": self._normalize_csv_list(router_data.get("roles", [])),
            "ospf_areas": router_data.get("ospf_areas", []) if router_data.get("ospf_areas") else [],
            "customer_net": router_data.get("customer_net"),
            "isp_name": router_data.get("isp_name"),
            "asn": router_data.get("asn"),
            "public_block": router_data.get("public_block"),
            "location": router_data.get("location"),
            "neighbors": neighbors
        }

    def get_ip_to_hostname_map(self) -> Dict[str, str]:
        """
        Build IP→hostname mapping for deployed routers only.

        This is used by the state monitor to map BGP peer IPs to hostnames.

        Returns:
            Dictionary mapping IP addresses to router hostnames.
        """
        deployed = self.get_deployed_hostnames()
        ip_map = {}

        for hostname, router_data in self.tfvars_data.get("routers", {}).items():
            if hostname not in deployed:
                continue

            # Add loopback IP
            loopback_ip = router_data.get("loopback_ip")
            if loopback_ip:
                # Strip CIDR notation
                ip = loopback_ip.split("/")[0]
                ip_map[ip] = hostname

            # Add interface IPs
            for interface in router_data.get("interfaces", []):
                interface_ip = interface.get("ip")
                if interface_ip:
                    ip_map[interface_ip] = hostname

        logger.info(f"Built IP mapping with {len(ip_map)} entries for deployed routers")
        return ip_map

    def reload(self):
        """
        Reload both Proxmox container list and tfvars data.

        This should be called after terraform apply to refresh the topology.
        Also clears tracked mutations since the infrastructure was re-provisioned.
        """
        logger.info("Reloading topology data...")

        # Clear Proxmox cache
        self.deployed_hostnames_cache = None
        self.cache_timestamp = None

        # Reload tfvars
        self._load_tfvars()

        # Clear stale mutations — infrastructure was re-provisioned from scratch
        from services.mutation_tracker import mutation_tracker
        mutation_tracker.clear()
        logger.info("Cleared tracked mutations after topology reload")

        logger.info("Topology data reloaded successfully")

    def get_connection_interfaces(self, link_id: str) -> Optional[Dict[str, Any]]:
        """
        Get interface names for both ends of a link.

        Returns interface names (eth0, eth1, etc.) by finding the shared bridge
        and calculating interface index from position in interfaces array.

        Args:
            link_id: Link identifier (format: "router1:router2").

        Returns:
            Dictionary with source_interface and target_interface, or None if not found.
        """
        parts = link_id.split(':', 1)
        if len(parts) != 2:
            return None

        source_hostname, target_hostname = parts

        # Get router data from tfvars
        source_router = self.tfvars_data.get("routers", {}).get(source_hostname)
        target_router = self.tfvars_data.get("routers", {}).get(target_hostname)

        if not source_router or not target_router:
            return None

        # Find the shared bridge between the two routers
        source_interfaces = source_router.get("interfaces", [])
        target_interfaces = target_router.get("interfaces", [])

        source_bridges = {iface.get("bridge") for iface in source_interfaces if iface.get("bridge")}
        target_bridges = {iface.get("bridge") for iface in target_interfaces if iface.get("bridge")}

        shared_bridges = source_bridges & target_bridges
        if not shared_bridges:
            return None

        shared_bridge = list(shared_bridges)[0]

        # Find interface index for each router (eth0, eth1, etc.)
        source_iface_name = None
        target_iface_name = None

        for idx, iface in enumerate(source_interfaces):
            if iface.get("bridge") == shared_bridge:
                source_iface_name = f"eth{idx}"
                break

        for idx, iface in enumerate(target_interfaces):
            if iface.get("bridge") == shared_bridge:
                target_iface_name = f"eth{idx}"
                break

        if not source_iface_name or not target_iface_name:
            return None

        return {
            "link_id": link_id,
            "source_hostname": source_hostname,
            "target_hostname": target_hostname,
            "source_interface": source_iface_name,
            "target_interface": target_iface_name,
            "bridge": shared_bridge
        }

    def get_dns_servers(self) -> List[Dict[str, Any]]:
        """
        Get all DNS servers from tfvars, enriched with hosting router info and deployment status.

        Returns:
            List of DNS server dictionaries.
        """
        deployed = self.get_deployed_hostnames()
        routers_data = self.tfvars_data.get("routers", {})
        servers = []

        for hostname, dns_data in self.tfvars_data.get("core_dns", {}).items():
            router_hostname = dns_data.get("router")
            router_data = routers_data.get(router_hostname, {})

            # tfvars stores zones as comma-separated strings (e.g. ['com,net,org'])
            raw_zones = dns_data.get("zones", [])
            zones = []
            for entry in raw_zones:
                zones.extend(entry.split(","))

            servers.append({
                "hostname": hostname,
                "role": dns_data.get("role"),
                "zones": zones,
                "ip_address": dns_data.get("ip_address"),
                "subnet": dns_data.get("subnet"),
                "router": router_hostname,
                "router_location": router_data.get("location"),
                "router_isp": router_data.get("isp_name"),
                "cores": dns_data.get("cores"),
                "memory": dns_data.get("memory"),
                "disk": dns_data.get("disk"),
                "deployed": hostname in deployed,
            })

        logger.info(f"Retrieved {len(servers)} DNS servers")
        return servers

    def get_ca_servers(self) -> List[Dict[str, Any]]:
        """Get all CA servers from tfvars, enriched with hosting router info and deployment status."""
        deployed = self.get_deployed_hostnames()
        routers_data = self.tfvars_data.get("routers", {})
        servers = []

        for hostname, ca_data in self.tfvars_data.get("core_ca", {}).items():
            router_hostname = ca_data.get("router")
            router_data = routers_data.get(router_hostname, {})

            servers.append({
                "hostname": hostname,
                "ca_name": ca_data.get("ca_name"),
                "role": ca_data.get("role"),
                "ip_address": ca_data.get("ip_address"),
                "subnet": ca_data.get("subnet"),
                "router": router_hostname,
                "router_location": router_data.get("location"),
                "router_isp": router_data.get("isp_name"),
                "cores": ca_data.get("cores"),
                "memory": ca_data.get("memory"),
                "disk": ca_data.get("disk"),
                "deployed": hostname in deployed,
            })

        logger.info(f"Retrieved {len(servers)} CA servers")
        return servers

    def get_router_attached(self, hostname: str) -> List[Dict[str, Any]]:
        """The core DNS servers and CAs on a router."""
        items = []

        for dns_hostname, dns_data in self.tfvars_data.get("core_dns", {}).items():
            if dns_data.get("router") == hostname:
                raw_zones = dns_data.get("zones", [])
                zones = []
                for entry in raw_zones:
                    zones.extend(entry.split(","))
                items.append({
                    "hostname": dns_hostname,
                    "type": dns_data.get("role"),
                    "category": "dns",
                    "ip_address": dns_data.get("ip_address"),
                    "subnet": dns_data.get("subnet"),
                    "role": dns_data.get("role"),
                    "zones": zones,
                })

        for ca_hostname, ca_data in self.tfvars_data.get("core_ca", {}).items():
            if ca_data.get("router") == hostname:
                items.append({
                    "hostname": ca_hostname,
                    "type": "ca",
                    "category": "ca",
                    "ip_address": ca_data.get("ip_address"),
                    "subnet": ca_data.get("subnet"),
                    "role": ca_data.get("ca_name"),
                })

        return items

    def get_customer_interfaces(self, hostname: str) -> List[Dict[str, Any]]:
        """
        Get customer-facing interfaces for a router.

        Customer interfaces are those on bridges with only 1 router connected
        (not backbone links which have 2 routers).

        Args:
            hostname: Router hostname.

        Returns:
            List of customer interface dictionaries.
        """
        deployed = self.get_deployed_hostnames()
        if hostname not in deployed:
            return []

        router_data = self.tfvars_data.get("routers", {}).get(hostname)
        if not router_data:
            return []

        # Build a map of bridge -> list of routers using that bridge
        bridge_to_routers: Dict[str, List[str]] = defaultdict(list)
        for h, r_data in self.tfvars_data.get("routers", {}).items():
            if h not in deployed:
                continue
            for iface in r_data.get("interfaces", []):
                bridge = iface.get("bridge")
                if bridge:
                    bridge_to_routers[bridge].append(h)

        # Find interfaces on bridges with only this router (customer networks)
        customer_interfaces = []
        for idx, iface in enumerate(router_data.get("interfaces", [])):
            bridge = iface.get("bridge")
            if not bridge:
                continue

            # Customer interface = bridge with only 1 router
            if len(bridge_to_routers.get(bridge, [])) == 1:
                customer_interfaces.append({
                    "hostname": hostname,
                    "interface_name": f"eth{idx}",
                    "customer_net": router_data.get("customer_net"),
                    "bridge": bridge,
                    "ip": iface.get("ip"),
                    "cidr": iface.get("cidr")
                })

        return customer_interfaces


# Global topology service instance
topology_service = TopologyService()
