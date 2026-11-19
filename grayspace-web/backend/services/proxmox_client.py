"""Proxmox API client for container management."""
from typing import List, Dict, Any, Optional, Tuple
from proxmoxer import ProxmoxAPI
from config import settings
from services import dev_simulation
import ipaddress
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)


def tag_list(tags: Optional[str]) -> List[str]:
    """Proxmox returns a guest's tags as one ';'-separated string (older versions also used ','
    or spaces)."""
    return [t for t in re.split(r"[;, ]+", tags or "") if t]


def has_tag(tags: Optional[str], tag: str) -> bool:
    """Whether a guest carries a tag. Slice guests carry environment_id and slice-<instance>, so
    deployment membership is tested on the list, never on the whole string (C7)."""
    return tag in tag_list(tags)


class ProxmoxClient:
    """Client for Proxmox API operations."""

    def __init__(self):
        self.proxmox: Optional[ProxmoxAPI] = None
        self._connected = False
        # The deployment's guests, as of when (time.monotonic()); see get_deployment_containers
        self._guests: Optional[Tuple[float, List[Dict[str, Any]]]] = None
        self._guests_lock = threading.Lock()

    def invalidate_guests(self):
        """Forget the cached guest list: a guest was started, stopped, restarted or changed."""
        self._guests = None

    def _connect(self):
        """Establish connection to Proxmox API (lazy initialization)."""
        if self._connected or dev_simulation.enabled():
            return

        try:
            # Support both token and user/password authentication
            if settings.proxmox_api_token:
                # Token authentication (format: "USER@REALM!TOKENID=UUID")
                token_parts = settings.proxmox_api_token.split('=')
                if len(token_parts) != 2 or '!' not in token_parts[0]:
                    raise ValueError("Invalid token format. Expected: USER@REALM!TOKENID=UUID")
                # proxmoxer builds "USER@REALM!TOKENID" itself, so the user and the token ID
                # are passed separately.
                user, token_name = token_parts[0].split('!', 1)
                token_value = token_parts[1]  # UUID

                self.proxmox = ProxmoxAPI(
                    settings.proxmox_api_host,
                    user=user,
                    token_name=token_name,
                    token_value=token_value,
                    verify_ssl=settings.proxmox_verify_ssl
                )
                logger.info(f"Connected to Proxmox at {settings.proxmox_api_host} using API token")
            elif settings.proxmox_api_user and settings.proxmox_api_password:
                # User/password authentication
                self.proxmox = ProxmoxAPI(
                    settings.proxmox_api_host,
                    user=settings.proxmox_api_user,
                    password=settings.proxmox_api_password,
                    verify_ssl=settings.proxmox_verify_ssl
                )
                logger.info(f"Connected to Proxmox at {settings.proxmox_api_host} using user/password")
            else:
                raise ValueError("Either proxmox_api_token or (proxmox_api_user and proxmox_api_password) must be provided")

            self._connected = True
        except Exception as e:
            logger.error(f"Failed to connect to Proxmox: {e}")
            raise

    def get_deployment_containers(self) -> List[Dict[str, Any]]:
        """
        Get all containers for this deployment (filtered by environment_id tag).

        CRITICAL: Only returns containers matching this deployment's environment_id.
        This ensures multi-developer isolation.

        Returns:
            List of container dictionaries with vmid, name, node, status, and tags.
        """
        if dev_simulation.enabled():
            return dev_simulation.containers()

        # One list for every caller within proxmox_guest_cache_seconds; the lock makes callers
        # that arrive together share one request rather than each send their own
        with self._guests_lock:
            cached = self._guests
            if cached and time.monotonic() - cached[0] < settings.proxmox_guest_cache_seconds:
                return [dict(c) for c in cached[1]]
            containers = self._list_guests()
            if containers is not None:
                self._guests = (time.monotonic(), containers)
            return [dict(c) for c in containers or []]

    def _list_guests(self) -> Optional[List[Dict[str, Any]]]:
        """This deployment's LXC containers and QEMU VMs, or None when Proxmox fails.

        /cluster/resources?type=vm lists every guest of the cluster, both kinds (type: lxc or
        qemu), from the status Proxmox keeps current itself: tens of milliseconds, where asking
        each node for its lxc and qemu lists makes it work out every container's status (seconds
        on a node with a few hundred). The status may be a few seconds behind; start, stop and
        restart forget the cache, and get_container_status asks about one guest directly."""
        # Lazy connection on first use
        self._connect()
        environment_id = settings.environment_id
        try:
            resources = self.proxmox.cluster.resources.get(type="vm")
        except Exception as e:
            logger.error(f"Error retrieving containers: {e}")
            self._connected = False
            logger.info("Marked Proxmox connection as disconnected — will reconnect on next call")
            return None
        containers = [
            {
                "vmid": guest["vmid"],
                "name": guest.get("name", ""),
                "node": guest.get("node", ""),
                "status": guest.get("status", "unknown"),
                "tags": guest.get("tags", ""),
                "uptime": guest.get("uptime", 0),
                "maxmem": guest.get("maxmem", 0),
                "maxdisk": guest.get("maxdisk", 0),
                "resource_type": "qemu" if guest.get("type") == "qemu" else "lxc",
            }
            # CRITICAL: Filter by environment_id tag (pattern from manual_cleanup.py:36)
            for guest in resources
            if guest.get("type") in ("lxc", "qemu") and has_tag(guest.get("tags"), environment_id)
        ]
        logger.debug(f"Retrieved {len(containers)} containers/VMs for environment '{environment_id}'")
        return containers

    def get_locked_guests(self) -> Optional[List[Dict[str, Any]]]:
        """Every guest of the cluster that holds a lock (create, backup, migrate...), tagged or not, or None
        when Proxmox fails. A create that dies part way leaves an untagged CT<vmid> holding the create lock,
        so this doesn't filter by environment_id: callers match VM IDs they know."""
        if dev_simulation.enabled():
            return []
        try:
            self._connect()
            resources = self.proxmox.cluster.resources.get(type="vm")
        except Exception as e:
            logger.error(f"Error retrieving guests: {e}")
            self._connected = False
            return None
        return [
            {"vmid": g["vmid"], "name": g.get("name", ""), "node": g.get("node", ""), "status": g.get("status", "unknown"),
             "lock": g["lock"], "resource_type": "qemu" if g.get("type") == "qemu" else "lxc"}
            for g in resources if g.get("type") in ("lxc", "qemu") and g.get("lock")
        ]

    def get_node_status(self) -> Optional[Dict[str, Any]]:
        """
        Get the configured node's status (CPU, memory), or None if Proxmox is unavailable.
        """
        if dev_simulation.enabled():
            return dev_simulation.node_status()
        try:
            self._connect()
            return self.proxmox.nodes(settings.proxmox_node).status.get()
        except Exception as e:
            logger.error(f"Error retrieving node status: {e}")
            self._connected = False
            return None

    def get_storage_status(self, storage: str) -> Optional[Dict[str, Any]]:
        """A storage's status on the configured node (total, used, avail in bytes), or None."""
        if dev_simulation.enabled():
            return dev_simulation.storage_status()
        try:
            self._connect()
            return self.proxmox.nodes(settings.proxmox_node).storage(storage).status.get()
        except Exception as e:
            logger.error(f"Error retrieving storage {storage} status: {e}")
            self._connected = False
            return None

    def get_node_storages(self) -> Optional[List[Dict[str, Any]]]:
        """The configured node's storages (storage, type, content, enabled...), or None if Proxmox is unavailable."""
        if dev_simulation.enabled():
            return None
        try:
            self._connect()
            return self.proxmox.nodes(settings.proxmox_node).storage.get()
        except Exception as e:
            logger.error(f"Error listing storages: {e}")
            self._connected = False
            return None

    def get_node_networks(self) -> Optional[List[Dict[str, Any]]]:
        """The configured node's network interfaces and bridges (iface, type...), or None."""
        if dev_simulation.enabled():
            return None
        try:
            self._connect()
            return self.proxmox.nodes(settings.proxmox_node).network.get()
        except Exception as e:
            logger.error(f"Error listing networks: {e}")
            self._connected = False
            return None

    def get_sdn_vnets(self) -> Optional[List[Dict[str, Any]]]:
        """The cluster's SDN vnets (vnet, zone...), or None when SDN can't be read."""
        if dev_simulation.enabled():
            return None
        try:
            self._connect()
            return self.proxmox.cluster.sdn.vnets.get()
        except Exception as e:
            logger.error(f"Error listing SDN vnets: {e}")
            self._connected = False
            return None

    def get_sdn_zones(self) -> Optional[List[Dict[str, Any]]]:
        """The cluster's SDN zones, or None when SDN can't be read."""
        if dev_simulation.enabled():
            return None
        try:
            self._connect()
            return self.proxmox.cluster.sdn.zones.get()
        except Exception as e:
            logger.error(f"Error listing SDN zones: {e}")
            self._connected = False
            return None

    def get_instance_containers(self, instance: str) -> List[Dict[str, Any]]:
        """This deployment's guests that belong to a slice instance (tagged slice-<instance>)."""
        return [c for c in self.get_deployment_containers() if has_tag(c.get("tags"), f"slice-{instance}")]

    def get_container_by_hostname(self, hostname: str) -> Optional[Dict[str, Any]]:
        """
        Get container information by hostname.

        Args:
            hostname: Container hostname.

        Returns:
            Container dictionary or None if not found or not in this deployment.
        """
        containers = self.get_deployment_containers()
        for container in containers:
            if container["name"] == hostname:
                return container
        return None

    def _resource_api(self, node: str, vmid: int, resource_type: str = "lxc"):
        """Get the appropriate Proxmox API endpoint for a resource (lxc or qemu)."""
        if resource_type == "qemu":
            return self.proxmox.nodes(node).qemu(vmid)
        return self.proxmox.nodes(node).lxc(vmid)

    def get_container_status(self, node: str, vmid: int) -> Optional[Dict[str, Any]]:
        """
        Get detailed status of a container.

        Args:
            node: Proxmox node name.
            vmid: Container VMID.

        Returns:
            Status dictionary or None on error.
        """
        if dev_simulation.enabled():
            match = next((c for c in dev_simulation.containers() if c["vmid"] == vmid), None)
            return {"status": match["status"], "vmid": vmid, "name": match["name"]} if match else None
        self._connect()
        try:
            status = self.proxmox.nodes(node).lxc(vmid).status.current.get()
            return status
        except Exception as e:
            logger.error(f"Error getting container status: {e}")
            self._connected = False
            return None

    def start_container(self, hostname: str) -> bool:
        """
        Start a container.

        Args:
            hostname: Container hostname.

        Returns:
            True on success, False on error.
        """
        self._connect()
        container = self.get_container_by_hostname(hostname)
        if not container:
            logger.error(f"Container {hostname} not found or not in this deployment")
            return False

        # Verify container belongs to this deployment (double-check)
        if not has_tag(container["tags"], settings.environment_id):
            logger.error(f"Container {hostname} does not belong to environment {settings.environment_id}")
            return False

        try:
            node = container["node"]
            vmid = container["vmid"]
            if dev_simulation.enabled():
                return dev_simulation.set_running(hostname, True)
            self._resource_api(node, vmid, container.get("resource_type", "lxc")).status.start.post()
            self.invalidate_guests()
            logger.info(f"Started container {hostname} (vmid={vmid})")
            return True
        except Exception as e:
            logger.error(f"Error starting container {hostname}: {e}")
            self._connected = False
            return False

    def stop_container(self, hostname: str) -> bool:
        """
        Stop a container.

        Args:
            hostname: Container hostname.

        Returns:
            True on success, False on error.
        """
        container = self.get_container_by_hostname(hostname)
        if not container:
            logger.error(f"Container {hostname} not found or not in this deployment")
            return False

        # Verify container belongs to this deployment (double-check)
        if not has_tag(container["tags"], settings.environment_id):
            logger.error(f"Container {hostname} does not belong to environment {settings.environment_id}")
            return False

        try:
            node = container["node"]
            vmid = container["vmid"]
            if dev_simulation.enabled():
                return dev_simulation.set_running(hostname, False)
            self._resource_api(node, vmid, container.get("resource_type", "lxc")).status.stop.post()
            self.invalidate_guests()
            logger.info(f"Stopped container {hostname} (vmid={vmid})")
            return True
        except Exception as e:
            logger.error(f"Error stopping container {hostname}: {e}")
            self._connected = False
            return False

    def restart_container(self, hostname: str) -> bool:
        """
        Restart a container.

        Args:
            hostname: Container hostname.

        Returns:
            True on success, False on error.
        """
        container = self.get_container_by_hostname(hostname)
        if not container:
            logger.error(f"Container {hostname} not found or not in this deployment")
            return False

        # Verify container belongs to this deployment (double-check)
        if not has_tag(container["tags"], settings.environment_id):
            logger.error(f"Container {hostname} does not belong to environment {settings.environment_id}")
            return False

        try:
            node = container["node"]
            vmid = container["vmid"]
            if dev_simulation.enabled():
                return dev_simulation.set_running(hostname, True)
            self._resource_api(node, vmid, container.get("resource_type", "lxc")).status.restart.post()
            self.invalidate_guests()
            logger.info(f"Restarted container {hostname} (vmid={vmid})")
            return True
        except Exception as e:
            logger.error(f"Error restarting container {hostname}: {e}")
            self._connected = False
            return False

    def get_container_management_ip(self, hostname: str, mgmt_subnet: Optional[str] = None) -> Optional[str]:
        """
        Get the management IP address of a container from Proxmox.

        Args:
            hostname: Container hostname.
            mgmt_subnet: Management network to match (default: settings.mgmt_subnet)

        Returns:
            Management IP address or None if not found.
        """
        if dev_simulation.enabled():
            return None  # simulated guests have no management network
        self._connect()
        container = self.get_container_by_hostname(hostname)
        if not container:
            logger.error(f"Container {hostname} not found")
            return None

        network = ipaddress.ip_network(mgmt_subnet or settings.mgmt_subnet, strict=False)

        def in_mgmt(ip: str) -> bool:
            try:
                return ipaddress.ip_address(ip) in network
            except ValueError:
                return False

        try:
            node = container["node"]
            vmid = container["vmid"]

            # Get container interfaces from Proxmox
            resource_type = container.get("resource_type", "lxc")
            if resource_type == "qemu":
                interfaces = self.proxmox.nodes(node).qemu(vmid).agent("network-get-interfaces").get().get("result", [])
            else:
                interfaces = self.proxmox.nodes(node).lxc(vmid).interfaces.get()

            for iface in interfaces:
                # Skip loopback
                if iface.get("name") == "lo":
                    continue

                if resource_type == "qemu":
                    # QEMU guest agent format: ip-addresses list
                    for addr in iface.get("ip-addresses", []):
                        if addr.get("ip-address-type") == "ipv4":
                            ip = addr["ip-address"]
                            if in_mgmt(ip):
                                logger.info(f"Found management IP {ip} for {hostname}")
                                return ip
                else:
                    # LXC format: inet field with CIDR
                    inet = iface.get("inet")
                    if inet:
                        ip = inet.split("/")[0]
                        if in_mgmt(ip):
                            logger.info(f"Found management IP {ip} for {hostname}")
                            return ip

            logger.warning(f"No management IP found for {hostname} in subnet {mgmt_subnet}")
            return None
        except Exception as e:
            logger.error(f"Error getting management IP for {hostname}: {e}")
            self._connected = False
            return None


# Global Proxmox client instance
proxmox_client = ProxmoxClient()
