"""Prometheus client for querying BGP/OSPF state metrics."""
import asyncio
from typing import List, Dict, Any, Optional
import httpx
from config import settings
import logging

logger = logging.getLogger(__name__)


class PrometheusClient:
    """Client for Prometheus API queries."""

    def __init__(self):
        self.base_url = settings.prometheus_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=10.0, trust_env=False)

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()

    async def query(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Execute a PromQL query.

        Args:
            query: PromQL query string.

        Returns:
            Query result or None on error.
        """
        try:
            url = f"{self.base_url}/api/v1/query"
            response = await self.client.get(url, params={"query": query})
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "success":
                logger.error(f"Prometheus query failed: {data}")
                return None
            return data.get("data", {})
        except httpx.ConnectError as e:
            logger.error(f"Error connecting to Prometheus at {url}: {e}")
            return None
        except httpx.TimeoutException as e:
            logger.error(f"Timeout querying Prometheus at {url}: {e}")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error querying Prometheus: {e.response.status_code} {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Error querying Prometheus at {url}: {type(e).__name__}: {e}", exc_info=True)
            return None

    async def get_bgp_peer_states(self) -> List[Dict[str, Any]]:
        """
        Get BGP peer states from all routers.

        Returns:
            List of BGP peer state dictionaries with router, peer, and state info.
        """
        query = 'frr_bgp_peer_state{job="frr"}'
        result = await self.query(query)
        if not result:
            return []

        peer_states = []
        for metric in result.get("result", []):
            labels = metric.get("metric", {})
            value = metric.get("value", [None, None])[1]

            # Extract instance hostname (format: "hostname:9342")
            instance = labels.get("instance", "")
            hostname = instance.split(":")[0] if ":" in instance else instance

            # Map state value to string (frr_exporter: 0=down, 1=up, 2=shutdown)
            state_map = {
                "0": "down",
                "1": "up",
                "2": "shutdown"
            }
            state = state_map.get(str(value), "unknown")

            peer_states.append({
                "router": hostname,
                "peer_address": labels.get("peer", ""),
                "peer_asn": labels.get("peer_as", ""),
                "state": state,
                "vrf": labels.get("vrf", "default")
            })

        logger.info(f"Retrieved {len(peer_states)} BGP peer states from Prometheus")
        return peer_states

    async def get_ospf_neighbor_states(self) -> List[Dict[str, Any]]:
        """
        Get OSPF neighbor states from all routers.

        Returns:
            List of OSPF neighbor state dictionaries.
        """
        query = 'frr_ospf_neighbor_state{job="frr"}'
        result = await self.query(query)
        if not result:
            return []

        # OSPF state mapping (frr_exporter: 0-indexed)
        ospf_state_map = {
            "0": "down", "1": "attempt", "2": "init", "3": "twoway",
            "4": "exstart", "5": "exchange", "6": "loading", "7": "full"
        }

        neighbors = []
        for metric in result.get("result", []):
            labels = metric.get("metric", {})
            value = metric.get("value", [None, None])[1]

            instance = labels.get("instance", "")
            hostname = instance.split(":")[0] if ":" in instance else instance

            state = ospf_state_map.get(str(value), "unknown")

            neighbors.append({
                "router": hostname,
                "neighbor_address": labels.get("neighbor", ""),
                "neighbor_id": labels.get("id", ""),
                "area": labels.get("area", ""),
                "state": state,
                "is_up": state == "full"
            })

        logger.info(f"Retrieved {len(neighbors)} OSPF neighbor states from Prometheus")
        return neighbors

    async def get_interface_states(self) -> List[Dict[str, Any]]:
        """
        Get interface up/down states from node_exporter on all routers.

        Returns:
            List of interface state dictionaries.
        """
        query = 'node_network_up{job="node"}'
        result = await self.query(query)
        if not result:
            return []

        interfaces = []
        for metric in result.get("result", []):
            labels = metric.get("metric", {})
            value = metric.get("value", [None, None])[1]

            instance = labels.get("instance", "")
            hostname = instance.split(":")[0] if ":" in instance else instance
            device = labels.get("device", "")

            # Skip loopback
            if device == "lo":
                continue

            is_up = str(value) == "1"
            interfaces.append({
                "router": hostname,
                "interface": device,
                "state": "up" if is_up else "down",
                "is_up": is_up
            })

        logger.info(f"Retrieved {len(interfaces)} interface states from Prometheus")
        return interfaces

    async def get_router_state(self, hostname: str) -> Dict[str, Any]:
        """
        Get combined BGP, OSPF, and interface state for a single router.

        Args:
            hostname: Router hostname.

        Returns:
            Dictionary with bgp_peers, ospf_neighbors, and interfaces lists.
        """
        bgp_query = f'frr_bgp_peer_state{{job="frr", instance="{hostname}"}}'
        ospf_query = f'frr_ospf_neighbor_state{{job="frr", instance="{hostname}"}}'
        iface_query = f'node_network_up{{job="node", instance="{hostname}"}}'

        bgp_result, ospf_result, iface_result = await asyncio.gather(
            self.query(bgp_query),
            self.query(ospf_query),
            self.query(iface_query)
        )

        # Parse BGP peers (frr_exporter: 0=down, 1=up, 2=shutdown)
        bgp_state_map = {"0": "down", "1": "up", "2": "shutdown"}
        bgp_peers = []
        if bgp_result:
            for metric in bgp_result.get("result", []):
                labels = metric.get("metric", {})
                value = metric.get("value", [None, None])[1]
                bgp_peers.append({
                    "peer_address": labels.get("peer", ""),
                    "peer_hostname": "",
                    "peer_asn": labels.get("peer_as", ""),
                    "state": bgp_state_map.get(str(value), "unknown"),
                    "vrf": labels.get("vrf", "default")
                })

        # Parse OSPF neighbors (frr_exporter: 0-indexed)
        ospf_state_map = {
            "0": "down", "1": "attempt", "2": "init", "3": "twoway",
            "4": "exstart", "5": "exchange", "6": "loading", "7": "full"
        }
        ospf_neighbors = []
        if ospf_result:
            for metric in ospf_result.get("result", []):
                labels = metric.get("metric", {})
                value = metric.get("value", [None, None])[1]
                ospf_neighbors.append({
                    "neighbor_address": labels.get("neighbor", ""),
                    "neighbor_hostname": "",
                    "neighbor_id": labels.get("id", ""),
                    "area": labels.get("area", ""),
                    "state": ospf_state_map.get(str(value), "unknown")
                })

        # Parse interfaces
        interfaces = []
        if iface_result:
            for metric in iface_result.get("result", []):
                labels = metric.get("metric", {})
                value = metric.get("value", [None, None])[1]
                device = labels.get("device", "")
                if device == "lo":
                    continue
                interfaces.append({
                    "interface": device,
                    "state": "up" if str(value) == "1" else "down"
                })

        return {
            "bgp_peers": bgp_peers,
            "ospf_neighbors": ospf_neighbors,
            "interfaces": interfaces
        }

    async def get_interface_stats(self, hostname: str) -> List[Dict[str, Any]]:
        """
        Get per-interface traffic stats from node_exporter for a single router.

        Queries counters (bytes, packets, errors, drops), rates, and link speed.

        Args:
            hostname: Router hostname.

        Returns:
            List of per-interface stat dictionaries.
        """
        metrics = [
            ("rx_bytes_total", f'node_network_receive_bytes_total{{job="node", instance="{hostname}"}}'),
            ("tx_bytes_total", f'node_network_transmit_bytes_total{{job="node", instance="{hostname}"}}'),
            ("rx_packets_total", f'node_network_receive_packets_total{{job="node", instance="{hostname}"}}'),
            ("tx_packets_total", f'node_network_transmit_packets_total{{job="node", instance="{hostname}"}}'),
            ("rx_errs_total", f'node_network_receive_errs_total{{job="node", instance="{hostname}"}}'),
            ("tx_errs_total", f'node_network_transmit_errs_total{{job="node", instance="{hostname}"}}'),
            ("rx_drop_total", f'node_network_receive_drop_total{{job="node", instance="{hostname}"}}'),
            ("tx_drop_total", f'node_network_transmit_drop_total{{job="node", instance="{hostname}"}}'),
            ("rx_bytes_rate", f'rate(node_network_receive_bytes_total{{job="node", instance="{hostname}"}}[5m])'),
            ("tx_bytes_rate", f'rate(node_network_transmit_bytes_total{{job="node", instance="{hostname}"}}[5m])'),
            ("speed_bytes", f'node_network_speed_bytes{{job="node", instance="{hostname}"}}'),
        ]

        results = await asyncio.gather(
            *[self.query(q) for _, q in metrics]
        )

        # Aggregate results by device
        devices: Dict[str, Dict[str, Any]] = {}
        for (metric_name, _), result in zip(metrics, results):
            if not result:
                continue
            for item in result.get("result", []):
                labels = item.get("metric", {})
                device = labels.get("device", "")
                if device == "lo":
                    continue
                value = item.get("value", [None, None])[1]
                if device not in devices:
                    devices[device] = {"interface": device}
                try:
                    devices[device][metric_name] = float(value) if value is not None else None
                except (ValueError, TypeError):
                    devices[device][metric_name] = None

        return list(devices.values())

    async def get_link_traffic_rates(
        self, hostname1: str, iface1: str, hostname2: str, iface2: str
    ) -> Dict[str, Any]:
        """
        Get traffic rates for both endpoints of a link.

        Args:
            hostname1: First endpoint hostname.
            iface1: First endpoint interface name.
            hostname2: Second endpoint hostname.
            iface2: Second endpoint interface name.

        Returns:
            Dictionary with source and target endpoint rate data.
        """
        queries = [
            f'rate(node_network_receive_bytes_total{{job="node", instance="{hostname1}", device="{iface1}"}}[5m])',
            f'rate(node_network_transmit_bytes_total{{job="node", instance="{hostname1}", device="{iface1}"}}[5m])',
            f'rate(node_network_receive_bytes_total{{job="node", instance="{hostname2}", device="{iface2}"}}[5m])',
            f'rate(node_network_transmit_bytes_total{{job="node", instance="{hostname2}", device="{iface2}"}}[5m])',
        ]

        results = await asyncio.gather(*[self.query(q) for q in queries])

        def extract_rate(result: Optional[Dict]) -> Optional[float]:
            if not result or not result.get("result"):
                return None
            try:
                return float(result["result"][0]["value"][1])
            except (IndexError, KeyError, ValueError, TypeError):
                return None

        return {
            "source": {
                "hostname": hostname1,
                "interface": iface1,
                "rx_bytes_per_sec": extract_rate(results[0]),
                "tx_bytes_per_sec": extract_rate(results[1]),
            },
            "target": {
                "hostname": hostname2,
                "interface": iface2,
                "rx_bytes_per_sec": extract_rate(results[2]),
                "tx_bytes_per_sec": extract_rate(results[3]),
            },
        }

    async def get_router_metrics(self, hostname: str) -> Optional[Dict[str, Any]]:
        """
        Get all available metrics for a specific router.

        Args:
            hostname: Router hostname.

        Returns:
            Dictionary of metric values or None on error.
        """
        metrics = {}

        # BGP peers count
        query = f'count(frr_bgp_peer_state{{job="frr", instance="{hostname}"}})'
        result = await self.query(query)
        if result and result.get("result"):
            metrics["bgp_peers_total"] = int(result["result"][0]["value"][1])

        # BGP established peers count
        query = f'count(frr_bgp_peer_state{{job="frr", instance="{hostname}"}} == 1)'
        result = await self.query(query)
        if result and result.get("result"):
            metrics["bgp_peers_up"] = int(result["result"][0]["value"][1])
        else:
            metrics["bgp_peers_up"] = 0

        return metrics


# Global Prometheus client instance
prometheus_client = PrometheusClient()
