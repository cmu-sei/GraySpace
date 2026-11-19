"""Traffic shaping service using Linux tc/netem."""
import asyncio
import re
import logging
from typing import List, Optional, Tuple
from models.traffic import TrafficConfig, InterfaceInfo, LinkTrafficConfig
from services.ssh_client import ssh_client
from services.proxmox_client import proxmox_client
from services.topology_service import topology_service

logger = logging.getLogger(__name__)


class TrafficService:
    """Service for managing traffic shaping on router interfaces."""

    def _get_baseline_latency(self, hostname: str, interface: str) -> Optional[float]:
        """Get the deployment baseline latency for an interface from tfvars.

        The parser already halves the geographic latency per-end, so the value
        returned here is ready to apply directly to one interface via netem.
        """
        router_data = topology_service.tfvars_data.get("routers", {}).get(hostname)
        if not router_data:
            return None
        idx = int(interface.removeprefix("eth"))
        interfaces = router_data.get("interfaces", [])
        if idx >= len(interfaces):
            return None
        return interfaces[idx].get("latency_ms")

    async def get_container_ip(self, hostname: str) -> Optional[str]:
        """
        Get the management IP address of a container from Proxmox.

        Args:
            hostname: Container hostname.

        Returns:
            Management IP address or None if not found.
        """
        # Get management IP directly from Proxmox (10.0.0.0/16 network)
        mgmt_ip = await asyncio.to_thread(proxmox_client.get_container_management_ip, hostname)
        if mgmt_ip:
            return mgmt_ip

        # Fallback to loopback_ip from topology if Proxmox lookup fails
        logger.warning(f"Falling back to loopback_ip for {hostname}")
        routers = topology_service.get_routers_with_locations()
        for router in routers:
            if router['hostname'] == hostname:
                loopback = router.get('loopback_ip', '')
                if loopback:
                    return loopback.split('/')[0]
        return None

    async def get_interfaces(self, hostname: str) -> List[InterfaceInfo]:
        """
        Get list of network interfaces on a router with their current traffic config.

        Args:
            hostname: Router hostname.

        Returns:
            List of InterfaceInfo objects.
        """
        ip = await self.get_container_ip(hostname)
        if not ip:
            logger.error(f"Could not find IP for hostname {hostname}")
            return []

        try:
            # Get interface list
            exit_code, stdout, stderr = await ssh_client.execute_command(
                ip,
                "ip -o link show | awk '{print $2}' | sed 's/:$//' | grep -v '^lo$'"
            )

            if exit_code != 0:
                logger.error(f"Failed to get interfaces: {stderr}")
                return []

            interfaces = []
            for iface_name in stdout.strip().split('\n'):
                if not iface_name:
                    continue

                iface_info = await self._get_interface_info(ip, iface_name)
                if iface_info:
                    interfaces.append(iface_info)

            return interfaces
        except Exception as e:
            logger.error(f"Error getting interfaces for {hostname}: {e}")
            return []

    async def _get_interface_info(self, ip: str, interface: str) -> Optional[InterfaceInfo]:
        """Get detailed information about a single interface."""
        try:
            # Get IP address
            exit_code, stdout, _ = await ssh_client.execute_command(
                ip,
                f"ip -o addr show {interface} | awk '{{print $4}}' | head -1"
            )
            ip_addr = stdout.strip() if exit_code == 0 else None

            # Get MAC address
            exit_code, stdout, _ = await ssh_client.execute_command(
                ip,
                f"ip link show {interface} | grep ether | awk '{{print $2}}'"
            )
            mac = stdout.strip() if exit_code == 0 else None

            # Get state
            exit_code, stdout, _ = await ssh_client.execute_command(
                ip,
                f"ip link show {interface} | head -1 | grep -o 'state [A-Z]*' | awk '{{print $2}}'"
            )
            state = stdout.strip().lower() if exit_code == 0 and stdout.strip() else "unknown"

            # Get current tc config
            traffic_config = await self._get_current_tc_config(ip, interface)

            return InterfaceInfo(
                name=interface,
                ip_address=ip_addr,
                mac_address=mac,
                state=state,
                traffic_config=traffic_config
            )
        except Exception as e:
            logger.error(f"Error getting interface info for {interface}: {e}")
            return None

    async def _get_current_tc_config(self, ip: str, interface: str) -> Optional[TrafficConfig]:
        """Get current tc/netem configuration for an interface."""
        try:
            exit_code, stdout, _ = await ssh_client.execute_command(
                ip,
                f"tc qdisc show dev {interface}"
            )

            if exit_code != 0:
                return None

            has_netem = 'netem' in stdout
            has_tbf = 'tbf' in stdout

            if not has_netem and not has_tbf:
                return None

            config = TrafficConfig()

            if has_netem:
                # Parse netem parameters
                # Example: qdisc netem 8001: root refcnt 2 limit 1000 delay 100.0ms 20.0ms loss 5%

                # Parse delay (latency and jitter)
                delay_match = re.search(r'delay\s+([\d.]+)(\w+)(?:\s+([\d.]+)(\w+))?', stdout)
                if delay_match:
                    latency = float(delay_match.group(1))
                    latency_unit = delay_match.group(2)
                    if latency_unit == 's':
                        latency *= 1000
                    config.latency_ms = latency

                    if delay_match.group(3):
                        jitter = float(delay_match.group(3))
                        jitter_unit = delay_match.group(4)
                        if jitter_unit == 's':
                            jitter *= 1000
                        config.jitter_ms = jitter

                # Parse loss
                loss_match = re.search(r'loss\s+([\d.]+)%', stdout)
                if loss_match:
                    config.packet_loss_percent = float(loss_match.group(1))

                # netem with rate param
                rate_match = re.search(r'rate\s+([\d.]+)([KMG]?)bit', stdout)
                if rate_match:
                    rate = float(rate_match.group(1))
                    unit = rate_match.group(2)
                    if unit == 'K':
                        rate /= 1000
                    elif unit == 'G':
                        rate *= 1000
                    config.bandwidth_mbps = rate

            if has_tbf and config.bandwidth_mbps is None:
                # Parse tbf bandwidth
                rate_match = re.search(r'rate\s+([\d.]+)([KMG]?)bit', stdout)
                if rate_match:
                    rate = float(rate_match.group(1))
                    unit = rate_match.group(2)
                    if unit == 'K':
                        rate /= 1000
                    elif unit == 'G':
                        rate *= 1000
                    config.bandwidth_mbps = rate

            return config if config.has_any_config() else None
        except Exception as e:
            logger.error(f"Error getting tc config for {interface}: {e}")
            return None

    async def apply_traffic_config(
        self,
        hostname: str,
        interface: str,
        config: TrafficConfig
    ) -> bool:
        """
        Apply traffic shaping configuration to an interface.

        Args:
            hostname: Router hostname.
            interface: Network interface name.
            config: Traffic configuration to apply.

        Returns:
            True if successful.
        """
        ip = await self.get_container_ip(hostname)
        if not ip:
            logger.error(f"Could not find IP for hostname {hostname}")
            return False

        try:
            # First, clear any existing rules
            await self._clear_tc_rules(ip, interface)

            if not config.has_any_config():
                logger.info(f"No traffic shaping config to apply for {hostname}:{interface}")
                return True

            # Build tc commands
            commands = self._build_tc_commands(interface, config)

            for cmd in commands:
                logger.debug(f"Executing tc command on {hostname}: {cmd}")
                exit_code, stdout, stderr = await ssh_client.execute_command(ip, cmd)
                if exit_code != 0:
                    logger.error(f"tc command failed: {stderr}")
                    # Try to clean up on failure
                    await self._clear_tc_rules(ip, interface)
                    return False

            logger.info(f"Applied traffic shaping to {hostname}:{interface}")
            return True
        except Exception as e:
            logger.error(f"Error applying traffic config: {e}")
            return False

    async def clear_traffic_config(self, hostname: str, interface: str) -> bool:
        """
        Clear user traffic shaping and restore baseline latency from tfvars.

        Args:
            hostname: Router hostname.
            interface: Network interface name.

        Returns:
            True if successful.
        """
        ip = await self.get_container_ip(hostname)
        if not ip:
            logger.error(f"Could not find IP for hostname {hostname}")
            return False

        try:
            await self._clear_tc_rules(ip, interface)
            await self._restore_baseline(ip, hostname, interface)
            logger.info(f"Restored baseline traffic shaping on {hostname}:{interface}")
            return True
        except Exception as e:
            logger.error(f"Error clearing traffic config: {e}")
            return False

    async def _restore_baseline(self, ip: str, hostname: str, interface: str):
        """Re-apply the deployment baseline latency from tfvars."""
        baseline_ms = self._get_baseline_latency(hostname, interface)
        if baseline_ms and baseline_ms > 0:
            cmd = f"tc qdisc add dev {interface} root netem delay {baseline_ms}ms"
            exit_code, _, stderr = await ssh_client.execute_command(ip, cmd)
            if exit_code != 0:
                logger.error(f"Failed to restore baseline on {hostname}:{interface}: {stderr}")

    async def _clear_tc_rules(self, ip: str, interface: str):
        """Clear all tc rules from an interface."""
        # Delete root qdisc (this clears all rules)
        await ssh_client.execute_command(
            ip,
            f"tc qdisc del dev {interface} root 2>/dev/null || true"
        )

    def _build_tc_commands(self, interface: str, config: TrafficConfig) -> List[str]:
        """
        Build tc commands for the given configuration.

        Uses a combination of netem (for latency, jitter, loss) and tbf (for bandwidth).
        """
        commands = []

        # Determine if we need netem, tbf, or both
        needs_netem = (
            (config.latency_ms is not None and config.latency_ms > 0) or
            (config.jitter_ms is not None and config.jitter_ms > 0) or
            (config.packet_loss_percent is not None and config.packet_loss_percent > 0)
        )
        needs_tbf = config.bandwidth_mbps is not None

        if needs_netem and needs_tbf:
            # Both: use htb as root, attach netem and tbf as children
            # This is complex, so we'll use a simpler approach: netem with rate
            netem_cmd = self._build_netem_command(interface, config, with_rate=True)
            commands.append(netem_cmd)
        elif needs_netem:
            # Just netem
            netem_cmd = self._build_netem_command(interface, config, with_rate=False)
            commands.append(netem_cmd)
        elif needs_tbf:
            # Just bandwidth limiting with tbf
            tbf_cmd = self._build_tbf_command(interface, config)
            commands.append(tbf_cmd)

        return commands

    def _build_netem_command(self, interface: str, config: TrafficConfig, with_rate: bool = False) -> str:
        """Build netem qdisc command."""
        cmd = f"tc qdisc add dev {interface} root netem"

        if config.latency_ms is not None and config.latency_ms > 0:
            cmd += f" delay {config.latency_ms}ms"
            if config.jitter_ms is not None and config.jitter_ms > 0:
                cmd += f" {config.jitter_ms}ms"

        if config.packet_loss_percent is not None and config.packet_loss_percent > 0:
            cmd += f" loss {config.packet_loss_percent}%"
            if config.correlation_percent:
                cmd += f" {config.correlation_percent}%"

        if with_rate and config.bandwidth_mbps is not None:
            # netem supports rate limiting directly
            cmd += f" rate {config.bandwidth_mbps}mbit"

        return cmd

    def _build_tbf_command(self, interface: str, config: TrafficConfig) -> str:
        """Build tbf (token bucket filter) qdisc command for bandwidth limiting."""
        rate_mbit = config.bandwidth_mbps or 100
        # Calculate burst size (at least MTU-sized, typically 32KB for good performance)
        burst_kb = max(32, int(rate_mbit * 1000 / 8 / 10))  # 1/10th second of data
        latency_ms = 50  # Maximum latency for queued packets

        return f"tc qdisc add dev {interface} root tbf rate {rate_mbit}mbit burst {burst_kb}kb latency {latency_ms}ms"

    # Link traffic shaping methods (symmetrical application)

    async def get_link_traffic_config(self, link_id: str) -> Optional[LinkTrafficConfig]:
        """
        Get current traffic configuration for a link (from both ends).

        Args:
            link_id: Link identifier (format: "router1:router2").

        Returns:
            LinkTrafficConfig with current settings, or None if link not found.
        """
        # Get interface names for this link
        conn_info = topology_service.get_connection_interfaces(link_id)
        if not conn_info:
            logger.error(f"Could not find connection interfaces for link {link_id}")
            return None

        source_hostname = conn_info["source_hostname"]
        target_hostname = conn_info["target_hostname"]
        source_interface = conn_info["source_interface"]
        target_interface = conn_info["target_interface"]

        # Get traffic config from source end (both should be same if symmetrical)
        source_ip = await self.get_container_ip(source_hostname)
        if not source_ip:
            return None

        per_end_config = await self._get_current_tc_config(source_ip, source_interface)

        # Combine per-end values back into link-level values: each end contributes
        # half of the user-visible latency/jitter/loss, so double when reading.
        link_config = self._combine_link_config(per_end_config) if per_end_config else TrafficConfig()

        return LinkTrafficConfig(
            link_id=link_id,
            config=link_config,
            source_hostname=source_hostname,
            target_hostname=target_hostname,
            source_interface=source_interface,
            target_interface=target_interface
        )

    async def apply_link_traffic_config(
        self,
        link_id: str,
        config: TrafficConfig
    ) -> Tuple[bool, str]:
        """
        Apply traffic shaping configuration to both ends of a link (symmetrically).

        Args:
            link_id: Link identifier (format: "router1:router2").
            config: Traffic configuration to apply.

        Returns:
            Tuple of (success, message).
        """
        # Get interface names for this link
        conn_info = topology_service.get_connection_interfaces(link_id)
        if not conn_info:
            return False, f"Could not find connection interfaces for link {link_id}"

        source_hostname = conn_info["source_hostname"]
        target_hostname = conn_info["target_hostname"]
        source_interface = conn_info["source_interface"]
        target_interface = conn_info["target_interface"]

        # Split latency/jitter/loss across both ends so the user-entered value
        # reflects the total link-level effect, not per-interface egress.
        per_end_config = self._split_link_config(config)

        # Apply to source
        success_source = await self.apply_traffic_config(source_hostname, source_interface, per_end_config)
        if not success_source:
            return False, f"Failed to apply traffic config to {source_hostname}:{source_interface}"

        # Apply to target
        success_target = await self.apply_traffic_config(target_hostname, target_interface, per_end_config)
        if not success_target:
            # Try to rollback source
            await self.clear_traffic_config(source_hostname, source_interface)
            return False, f"Failed to apply traffic config to {target_hostname}:{target_interface}"

        logger.info(f"Applied symmetrical traffic shaping to link {link_id}")
        return True, f"Traffic shaping applied to both ends of {link_id}"

    @staticmethod
    def _split_link_config(config: TrafficConfig) -> TrafficConfig:
        """Halve latency, jitter, and packet loss for per-end application.

        Bandwidth is full-duplex (each direction independently rate-limited),
        so it passes through unchanged.
        """
        return config.model_copy(update={
            "latency_ms": config.latency_ms / 2 if config.latency_ms else config.latency_ms,
            "jitter_ms": config.jitter_ms / 2 if config.jitter_ms else config.jitter_ms,
            "packet_loss_percent": (
                config.packet_loss_percent / 2
                if config.packet_loss_percent else config.packet_loss_percent
            ),
        })

    @staticmethod
    def _combine_link_config(config: TrafficConfig) -> TrafficConfig:
        """Inverse of _split_link_config: reconstruct link-level value from one end."""
        return config.model_copy(update={
            "latency_ms": config.latency_ms * 2 if config.latency_ms else config.latency_ms,
            "jitter_ms": config.jitter_ms * 2 if config.jitter_ms else config.jitter_ms,
            "packet_loss_percent": (
                config.packet_loss_percent * 2
                if config.packet_loss_percent else config.packet_loss_percent
            ),
        })

    async def clear_link_traffic_config(self, link_id: str) -> Tuple[bool, str]:
        """
        Clear traffic shaping configuration from both ends of a link.

        Args:
            link_id: Link identifier (format: "router1:router2").

        Returns:
            Tuple of (success, message).
        """
        # Get interface names for this link
        conn_info = topology_service.get_connection_interfaces(link_id)
        if not conn_info:
            return False, f"Could not find connection interfaces for link {link_id}"

        source_hostname = conn_info["source_hostname"]
        target_hostname = conn_info["target_hostname"]
        source_interface = conn_info["source_interface"]
        target_interface = conn_info["target_interface"]

        # Clear from both ends
        success_source = await self.clear_traffic_config(source_hostname, source_interface)
        success_target = await self.clear_traffic_config(target_hostname, target_interface)

        if not success_source or not success_target:
            return False, f"Failed to clear traffic config from one or both ends of {link_id}"

        logger.info(f"Cleared traffic shaping from link {link_id}")
        return True, f"Traffic shaping cleared from both ends of {link_id}"


# Global traffic service instance
traffic_service = TrafficService()
