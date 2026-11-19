"""Network (vNET/Zone) management service using Proxmox SDN API."""
import logging
from typing import List, Optional
from models.network import VNetInfo, ZoneInfo, LinkNetworkInfo, SDNApplyResult, CustomerInterfaceInfo
from services.proxmox_client import proxmox_client
from services.topology_service import topology_service
from config import settings

logger = logging.getLogger(__name__)


def own_zones() -> set:
    """The deployment's SDN zones: {environment_id}1 (simple) and {environment_id}2 (vlan).

    Matched exactly, not by prefix: deployments "ab" and "abc" can share a cluster.
    """
    env_id = settings.environment_id
    return {f"{env_id}1", f"{env_id}2"}


class NetworkService:
    """Service for managing Proxmox SDN vNETs and zones."""

    def get_zones(self) -> List[ZoneInfo]:
        """
        Get all SDN zones filtered by environment_id.

        Returns:
            List of ZoneInfo objects for zones in this deployment.
        """
        try:
            proxmox_client._connect()
            zones_data = proxmox_client.proxmox.cluster.sdn.zones.get()

            zones = []
            env_id = settings.environment_id

            for zone in zones_data:
                zone_id = zone.get('zone', '')
                if zone_id in own_zones():
                    zones.append(ZoneInfo(
                        zone_id=zone_id,
                        type=zone.get('type', 'unknown'),
                        bridge=zone.get('bridge'),
                        nodes=zone.get('nodes'),
                        pending=bool(zone.get('pending')),
                        description=self._get_zone_description(zone_id, zone.get('type', ''))
                    ))

            logger.info(f"Retrieved {len(zones)} zones for environment '{env_id}'")
            return zones
        except Exception as e:
            logger.error(f"Error retrieving zones: {e}")
            return []

    def _get_zone_description(self, zone_id: str, zone_type: str) -> str:
        """Generate a human-readable description for a zone."""
        env_id = settings.environment_id
        if zone_id == f"{env_id}1":
            return "Isolated simulation network (internal only)"
        elif zone_id == f"{env_id}2":
            return "Bridged to external network via VLAN"
        return f"{zone_type} zone"

    def get_vnets(self) -> List[VNetInfo]:
        """
        Get all SDN vNETs filtered by environment_id.

        Returns:
            List of VNetInfo objects for vNETs in this deployment.
        """
        try:
            proxmox_client._connect()
            vnets_data = proxmox_client.proxmox.cluster.sdn.vnets.get()

            vnets = []
            env_id = settings.environment_id

            for vnet in vnets_data:
                vnet_id = vnet.get('vnet', '')
                zone = vnet.get('zone', '')

                if zone in own_zones():
                    vnets.append(VNetInfo(
                        vnet_id=vnet_id,
                        zone=zone,
                        alias=vnet.get('alias'),
                        tag=vnet.get('tag'),
                        vlanaware=bool(vnet.get('vlanaware')),
                        pending=bool(vnet.get('pending'))
                    ))

            logger.info(f"Retrieved {len(vnets)} vNETs for environment '{env_id}'")
            return vnets
        except Exception as e:
            logger.error(f"Error retrieving vNETs: {e}")
            return []

    def get_vnet(self, vnet_id: str) -> Optional[VNetInfo]:
        """
        Get information about a specific vNET.

        Args:
            vnet_id: vNET identifier.

        Returns:
            VNetInfo or None if not found.
        """
        vnets = self.get_vnets()
        for vnet in vnets:
            if vnet.vnet_id == vnet_id:
                return vnet
        return None

    def change_vnet_zone(self, vnet_id: str, new_zone_id: str, vlan_tag: Optional[int] = None) -> SDNApplyResult:
        """
        Change a vNET's zone assignment.

        Args:
            vnet_id: vNET identifier.
            new_zone_id: Target zone ID.
            vlan_tag: VLAN tag (required when moving to a VLAN zone).

        Returns:
            SDNApplyResult with success status and any warnings.
        """
        warnings = []

        # Validate zone belongs to this deployment
        if new_zone_id not in own_zones():
            return SDNApplyResult(
                success=False,
                message=f"Zone {new_zone_id} does not belong to this deployment",
                warnings=[]
            )

        # Validate vNET exists and belongs to this deployment
        vnet = self.get_vnet(vnet_id)
        if not vnet:
            return SDNApplyResult(
                success=False,
                message=f"vNET {vnet_id} not found or not in this deployment",
                warnings=[]
            )

        # Check if moving to VLAN zone (external access)
        zones = self.get_zones()
        target_zone = next((z for z in zones if z.zone_id == new_zone_id), None)

        if target_zone and target_zone.type == 'vlan':
            if vlan_tag is None:
                return SDNApplyResult(
                    success=False,
                    message="VLAN tag is required when moving to a VLAN zone",
                    warnings=[]
                )
            warnings.append(
                f"Moving to VLAN zone with tag {vlan_tag}. "
                "Ensure this VLAN exists on your physical infrastructure."
            )

        try:
            proxmox_client._connect()

            # Get current vNET config to include required fields
            current_config = proxmox_client.proxmox.cluster.sdn.vnets(vnet_id).get()

            # Build update params - zone is required, include digest if present
            update_params = {'zone': new_zone_id}
            if 'digest' in current_config:
                update_params['digest'] = current_config['digest']

            # Handle VLAN tag
            if vlan_tag is not None:
                # Include VLAN tag when moving to VLAN zone
                update_params['tag'] = vlan_tag
            elif target_zone and target_zone.type != 'vlan' and current_config.get('tag'):
                # Remove tag when moving away from VLAN zone
                update_params['delete'] = 'tag'

            # Update the vNET's zone
            proxmox_client.proxmox.cluster.sdn.vnets(vnet_id).put(**update_params)
            logger.info(f"Updated vNET {vnet_id} zone to {new_zone_id}" + (f" with tag {vlan_tag}" if vlan_tag else ""))

            # Apply SDN configuration
            apply_result = self._apply_sdn_config()
            if not apply_result:
                return SDNApplyResult(
                    success=False,
                    message="Zone change queued but SDN apply failed. Check Proxmox logs.",
                    warnings=warnings
                )

            return SDNApplyResult(
                success=True,
                message=f"vNET {vnet_id} moved to zone {new_zone_id}",
                warnings=warnings
            )
        except Exception as e:
            logger.error(f"Error changing vNET zone for {vnet_id} to {new_zone_id}: {e}")
            error_msg = str(e)
            # Extract more meaningful error from Proxmox response if available
            if hasattr(e, 'content'):
                error_msg = f"{error_msg} - {e.content}"
            return SDNApplyResult(
                success=False,
                message=error_msg,
                warnings=warnings
            )

    def _apply_sdn_config(self) -> bool:
        """
        Apply pending SDN configuration changes.

        Returns:
            True if apply was successful.
        """
        try:
            proxmox_client._connect()
            proxmox_client.proxmox.cluster.sdn.put()
            logger.info("SDN configuration applied")
            return True
        except Exception as e:
            logger.error(f"Error applying SDN config: {e}")
            return False

    def get_link_network_info(self, link_id: str) -> Optional[LinkNetworkInfo]:
        """
        Get network information for a router-to-router link.

        This maps link_id (format: "router1:router2") to the underlying vNET.

        Args:
            link_id: Link identifier.

        Returns:
            LinkNetworkInfo or None if not found.
        """
        # Parse link_id to get router hostnames (format: "router1:router2")
        parts = link_id.split(':', 1)
        if len(parts) != 2:
            return None

        source_hostname, target_hostname = parts

        # Get connection info from topology
        connections = topology_service.get_connections()
        link = None
        for conn in connections:
            if ((conn['source_hostname'] == source_hostname and conn['target_hostname'] == target_hostname) or
                (conn['source_hostname'] == target_hostname and conn['target_hostname'] == source_hostname)):
                link = conn
                break

        if not link:
            return None

        # Try to find the vNET for this link
        # vNETs are typically named based on the connection
        vnets = self.get_vnets()
        vnet_info = None
        zone_type = None

        # Look for vNET that might serve this link
        # This is heuristic - in practice, vNET naming follows terraform patterns
        for vnet in vnets:
            # Check if vNET name contains either hostname
            vnet_lower = vnet.vnet_id.lower()
            if source_hostname.lower() in vnet_lower or target_hostname.lower() in vnet_lower:
                vnet_info = vnet
                # Get zone type
                zones = self.get_zones()
                zone = next((z for z in zones if z.zone_id == vnet.zone), None)
                if zone:
                    zone_type = zone.type
                break

        return LinkNetworkInfo(
            link_id=link_id,
            vnet_id=vnet_info.vnet_id if vnet_info else None,
            zone=vnet_info.zone if vnet_info else None,
            zone_type=zone_type,
            subnet=link.get('subnet', ''),
            source_hostname=source_hostname,
            target_hostname=target_hostname,
            source_interface=link.get('source_interface'),
            target_interface=link.get('target_interface')
        )

    def get_customer_interfaces(self, hostname: str) -> List[CustomerInterfaceInfo]:
        """
        Get customer-facing interfaces for a router with their zone information.

        Args:
            hostname: Router hostname.

        Returns:
            List of CustomerInterfaceInfo objects.
        """
        # Get customer interfaces from topology service
        customer_ifaces = topology_service.get_customer_interfaces(hostname)
        if not customer_ifaces:
            return []

        vnets = self.get_vnets()
        zones = self.get_zones()

        result = []
        for iface in customer_ifaces:
            bridge = iface.get("bridge")
            customer_net = iface.get("customer_net")

            if not customer_net:
                continue

            # Find vNET for this bridge
            vnet_info = None
            zone_info = None
            for vnet in vnets:
                # vNET naming typically includes bridge reference
                if bridge and bridge.lower() in vnet.vnet_id.lower():
                    vnet_info = vnet
                    zone_info = next((z for z in zones if z.zone_id == vnet.zone), None)
                    break

            result.append(CustomerInterfaceInfo(
                hostname=hostname,
                interface_name=iface.get("interface_name"),
                customer_net=customer_net,
                bridge=bridge,
                vnet_id=vnet_info.vnet_id if vnet_info else None,
                zone=vnet_info.zone if vnet_info else None,
                zone_type=zone_info.type if zone_info else None,
                vlan_tag=vnet_info.tag if vnet_info else None
            ))

        return result

    def get_customer_interface_network_info(
        self,
        hostname: str,
        interface_name: str
    ) -> Optional[CustomerInterfaceInfo]:
        """
        Get network information for a specific customer interface.

        Args:
            hostname: Router hostname.
            interface_name: Interface name (e.g., eth2).

        Returns:
            CustomerInterfaceInfo or None if not found.
        """
        customer_ifaces = self.get_customer_interfaces(hostname)
        for iface in customer_ifaces:
            if iface.interface_name == interface_name:
                return iface
        return None

    def change_customer_interface_zone(
        self,
        hostname: str,
        interface_name: str,
        new_zone_id: str,
        vlan_tag: Optional[int] = None
    ) -> SDNApplyResult:
        """
        Change the zone for a customer interface's vNET.

        Args:
            hostname: Router hostname.
            interface_name: Interface name.
            new_zone_id: Target zone ID.
            vlan_tag: VLAN tag (required when moving to a VLAN zone).

        Returns:
            SDNApplyResult with success status and any warnings.
        """
        # Get interface info
        iface_info = self.get_customer_interface_network_info(hostname, interface_name)
        if not iface_info:
            return SDNApplyResult(
                success=False,
                message=f"Interface {interface_name} not found on {hostname} or is not a customer interface",
                warnings=[]
            )

        if not iface_info.vnet_id:
            return SDNApplyResult(
                success=False,
                message=f"No vNET associated with interface {interface_name}",
                warnings=[]
            )

        # Use existing vNET zone change method
        return self.change_vnet_zone(iface_info.vnet_id, new_zone_id, vlan_tag)


# Global network service instance
network_service = NetworkService()
