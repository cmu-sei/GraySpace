"""Background task for monitoring network state and broadcasting updates."""
import asyncio
from typing import Dict, List, Tuple
from datetime import datetime
from services.prometheus_client import prometheus_client
from services import events
from services.websocket_manager import websocket_manager
from services.topology_service import topology_service
from config import settings
import logging

logger = logging.getLogger(__name__)


class StateMonitor:
    """Monitors composite link state and broadcasts changes via WebSocket."""

    def __init__(self):
        self.previous_link_states: Dict[str, Dict] = {}
        self.ip_to_hostname: Dict[str, str] = {}
        self.link_topology: Dict[str, Dict] = {}
        self.previous_guest_statuses: Dict[str, str] = {}
        self.running = False

    async def _load_ip_mapping(self):
        """Load IP->hostname mapping from topology service."""
        try:
            self.ip_to_hostname = topology_service.get_ip_to_hostname_map()
            logger.info(f"Loaded {len(self.ip_to_hostname)} IP mappings for deployed routers")
        except Exception as e:
            logger.error(f"Error loading IP mapping: {e}")
            self.ip_to_hostname = {}

    def _load_link_topology(self):
        """Build link-to-interface mapping from topology service."""
        try:
            connections = topology_service.get_connections()
            self.link_topology = {}
            for conn in connections:
                link_id = conn["id"]
                self.link_topology[link_id] = {
                    "source_hostname": conn["source_hostname"],
                    "target_hostname": conn["target_hostname"],
                    "source_interface": conn.get("source_interface"),
                    "target_interface": conn.get("target_interface"),
                    "source_ip": conn.get("source_ip"),
                    "target_ip": conn.get("target_ip"),
                }
            logger.info(f"Loaded link topology with {len(self.link_topology)} links")
        except Exception as e:
            logger.error(f"Error loading link topology: {e}")
            self.link_topology = {}

    def _make_pair_key(self, a: str, b: str) -> Tuple[str, str]:
        """Create a sorted pair key for router-to-router lookups."""
        return tuple(sorted([a, b]))

    async def start(self):
        """Start the state monitoring loop."""
        self.running = True
        logger.info("State monitor started")

        await self._load_ip_mapping()
        self._load_link_topology()

        while self.running:
            try:
                await self._poll_and_broadcast()
            except Exception as e:
                logger.error(f"Error in state monitor loop: {e}")
            try:
                await self._poll_guests()
            except Exception as e:
                logger.error(f"Error polling guest statuses: {e}")

            await asyncio.sleep(settings.state_poll_interval)

    async def stop(self):
        """Stop the state monitoring loop."""
        self.running = False
        logger.info("State monitor stopped")

    async def _poll_and_broadcast(self):
        """Poll Prometheus for BGP, OSPF, and interface states; build composite link state."""
        bgp_states, ospf_states, iface_states = await asyncio.gather(
            prometheus_client.get_bgp_peer_states(),
            prometheus_client.get_ospf_neighbor_states(),
            prometheus_client.get_interface_states()
        )

        # Index BGP states by sorted router pair
        bgp_index: Dict[Tuple[str, str], str] = {}
        for peer in bgp_states:
            peer_hostname = self.ip_to_hostname.get(peer["peer_address"], peer["peer_address"])
            pair = self._make_pair_key(peer["router"], peer_hostname)
            # Keep worst state if multiple sessions between same pair
            existing = bgp_index.get(pair)
            if existing != "down":
                bgp_index[pair] = peer["state"]

        # Index OSPF states by sorted router pair
        ospf_index: Dict[Tuple[str, str], str] = {}
        for neighbor in ospf_states:
            neighbor_hostname = self.ip_to_hostname.get(neighbor["neighbor_address"], neighbor["neighbor_address"])
            pair = self._make_pair_key(neighbor["router"], neighbor_hostname)
            existing = ospf_index.get(pair)
            if existing != "down":
                ospf_index[pair] = neighbor["state"]

        # Index interface states by (router, device)
        iface_index: Dict[Tuple[str, str], str] = {}
        for iface in iface_states:
            iface_index[(iface["router"], iface["interface"])] = iface["state"]

        current_link_states: Dict[str, Dict] = {}
        changes = []

        for link_id, topo in self.link_topology.items():
            src = topo["source_hostname"]
            tgt = topo["target_hostname"]
            src_iface = topo.get("source_interface")
            tgt_iface = topo.get("target_interface")
            pair = self._make_pair_key(src, tgt)

            diagnostics: List[str] = []
            has_any_data = False

            # Check interface states
            if src_iface:
                src_iface_state = iface_index.get((src, src_iface))
                if src_iface_state is not None:
                    has_any_data = True
                    if src_iface_state == "down":
                        diagnostics.append(f"{src_iface} on {src} is down")
            if tgt_iface:
                tgt_iface_state = iface_index.get((tgt, tgt_iface))
                if tgt_iface_state is not None:
                    has_any_data = True
                    if tgt_iface_state == "down":
                        diagnostics.append(f"{tgt_iface} on {tgt} is down")

            # Check OSPF adjacency
            ospf_state = ospf_index.get(pair)
            if ospf_state is not None:
                has_any_data = True
                if ospf_state != "full":
                    diagnostics.append(f"OSPF adjacency: {ospf_state} (not Full)")

            # Check BGP session
            bgp_state = bgp_index.get(pair)
            if bgp_state is not None:
                has_any_data = True
                if bgp_state == "down":
                    diagnostics.append("BGP session is down")
                elif bgp_state == "shutdown":
                    diagnostics.append("BGP session is shutdown")

            # Determine composite state
            if not has_any_data:
                state = "unknown"
            elif len(diagnostics) > 0:
                state = "down"
            else:
                state = "up"

            current_link_states[link_id] = {
                "state": state,
                "diagnostics": diagnostics,
            }

            # Check for state change
            previous = self.previous_link_states.get(link_id, {})
            if previous.get("state") != state or previous.get("diagnostics") != diagnostics:
                change = {
                    "type": "link_state_update",
                    "link_id": link_id,
                    "state": state,
                    "diagnostics": diagnostics,
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }
                changes.append(change)
                if previous.get("state") != state:
                    logger.info(f"Link state change: {link_id}: {previous.get('state')} -> {state}")

        self.previous_link_states = current_link_states

        if changes:
            from services.map_layer import link_color
            from services.slices.base import links as base_links

            types = {link_id: link["type"] for link_id, link in base_links(topology_service.tfvars_data).items()}
            for change in changes:
                await websocket_manager.broadcast(change)
                # The same update for map layers (/api/v1/events), keyed by the feature id in /map
                events.feature_update(change["link_id"], "link", {
                    "state": change["state"],
                    "diagnostics": change["diagnostics"],
                    "color": link_color(change["state"], types.get(change["link_id"], "INTERNAL_LINK")),
                })
            logger.info(f"Broadcasted {len(changes)} link state changes")

    async def _poll_guests(self):
        """Publish guest.update when a base guest's Proxmox status changes (/api/v1/events), and a
        feature.update for routers. The first answer from Proxmox is only recorded."""
        from services import inventory

        statuses = await inventory.proxmox_statuses()
        if statuses is None:
            return
        kinds = inventory.guest_kinds(topology_service.tfvars_data)
        current = {hostname: statuses.get(hostname, "missing") for hostname in kinds}
        if self.previous_guest_statuses:
            for hostname, status in current.items():
                previous = self.previous_guest_statuses.get(hostname)
                if previous is None or previous == status:
                    continue
                logger.info(f"Guest status change: {hostname}: {previous} -> {status}")
                events.guest_update(hostname, kinds[hostname], status, previous)
                if kinds[hostname] == "router":
                    events.feature_update(hostname, "router", {"status": status})
        self.previous_guest_statuses = current


# Global state monitor instance
state_monitor = StateMonitor()
