/**
 * Type definitions for GraySpace application
 */

export interface Location {
  name: string;
  latitude: number;
  longitude: number;
}

export interface Router {
  hostname: string;
  loopback_ip: string;
  roles: string[];
  ospf_areas: string[];
  isp_name: string;
  asn: number;
  location: Location;
  customer_net?: string;
}

export interface RouterDetail extends Router {
  public_block: string;
  neighbors: Neighbor[];
}

export interface Neighbor {
  hostname: string;
  ip: string;
  type: string;
  asn: number;
}

export interface Link {
  id: string;
  source_hostname: string;
  target_hostname: string;
  type: "INTERNAL_LINK" | "PEERS_WITH" | "PROVIDES_TRANSIT_TO";
  subnet: string;
  source_ip: string;
  target_ip: string;
  ospf_area?: string;
  source_interface?: string;
  target_interface?: string;
}

export interface ISP {
  name: string;
  asn: number;
  public_block: string;
}

export interface BGPPeerState {
  router: string;
  peer_address: string;
  peer_asn: string;
  state: "up" | "down" | "shutdown" | "unknown";
  vrf: string;
}

export interface BGPStateUpdate {
  type: "bgp_state_update";
  router: string;
  peer: string;
  peer_ip: string;
  state: "up" | "down" | "shutdown";
  previous_state?: string;
  timestamp: string;
}

export interface Container {
  vmid: number;
  name: string;
  node: string;
  status: string;
  tags: string;
  uptime: number;
  maxmem: number;
  maxdisk: number;
}

export interface LinkState {
  state: "up" | "down" | "shutdown" | "unknown";
  diagnostics?: string[];
  timestamp?: string;
}

export interface LinkStateUpdate {
  type: "link_state_update";
  link_id: string;
  state: "up" | "down" | "shutdown" | "unknown";
  diagnostics: string[];
  timestamp: string;
}

export interface BGPPeerLiveState {
  peer_address: string;
  peer_hostname: string;
  peer_asn: string;
  state: "up" | "down" | "shutdown" | "unknown";
  vrf: string;
}

export interface OSPFNeighborLiveState {
  neighbor_address: string;
  neighbor_hostname: string;
  neighbor_id: string;
  area: string;
  state: string;
}

export interface InterfaceLiveState {
  interface: string;
  state: "up" | "down" | "unknown";
}

export interface RouterLiveState {
  bgp_peers: BGPPeerLiveState[];
  ospf_neighbors: OSPFNeighborLiveState[];
  interfaces: InterfaceLiveState[];
}

// Interface Traffic Stats (from node_exporter)
export interface InterfaceTrafficStats {
  interface: string;
  rx_bytes_total: number | null;
  tx_bytes_total: number | null;
  rx_packets_total: number | null;
  tx_packets_total: number | null;
  rx_errs_total: number | null;
  tx_errs_total: number | null;
  rx_drop_total: number | null;
  tx_drop_total: number | null;
  rx_bytes_rate: number | null;
  tx_bytes_rate: number | null;
  speed_bytes: number | null;
}

export interface RouterInterfaceStats {
  hostname: string;
  interfaces: InterfaceTrafficStats[];
}

export interface LinkEndpointRates {
  hostname: string;
  interface: string;
  rx_bytes_per_sec: number | null;
  tx_bytes_per_sec: number | null;
}

export interface LinkTrafficRates {
  source: LinkEndpointRates;
  target: LinkEndpointRates;
}

// Traffic Shaping Types
export interface TrafficConfig {
  latency_ms: number | null;
  jitter_ms: number | null;
  bandwidth_mbps: number | null;
  packet_loss_percent: number | null;
}

export interface InterfaceInfo {
  name: string;
  ip_address: string | null;
  mac_address: string | null;
  state: string;
  traffic_config: TrafficConfig | null;
}

// Network (vNET/Zone) Types
export interface ZoneInfo {
  zone_id: string;
  type: string;
  bridge: string | null;
  nodes: string | null;
  pending: boolean;
  description: string | null;
}

export interface VNetInfo {
  vnet_id: string;
  zone: string;
  alias: string | null;
  tag: number | null;
  vlanaware: boolean;
  pending: boolean;
}

export interface LinkNetworkInfo {
  link_id: string;
  vnet_id: string | null;
  zone: string | null;
  zone_type: string | null;
  subnet: string;
  source_hostname: string;
  target_hostname: string;
  source_interface: string | null;
  target_interface: string | null;
}

// Link traffic shaping (symmetrical application)
export interface LinkTrafficConfig {
  link_id: string;
  config: TrafficConfig;
  source_hostname: string;
  target_hostname: string;
  source_interface: string;
  target_interface: string;
}

// Customer interface zone info
export interface CustomerInterfaceInfo {
  hostname: string;
  interface_name: string;
  customer_net: string;
  bridge: string;
  vnet_id: string | null;
  zone: string | null;
  zone_type: string | null;
  vlan_tag: number | null;
}

// Reset Types
export interface ResetStatus {
  status: 'idle' | 'running' | 'completed' | 'failed';
  operation: 'quick' | null;
  start_time: string | null;
  is_running: boolean;
}

// DNS Server Types
export interface DNSServer {
  hostname: string;
  role: 'root-dns' | 'tld' | 'hosting' | 'resolver';
  zones: string[];
  ip_address: string;
  subnet: string;
  router: string;
  router_location: Location | null;
  router_isp: string;
  cores: number;
  memory: number;
  disk: number;
  deployed: boolean;
}

export interface ZoneRecord {
  name: string;
  ttl: number;
  class: string;
  type: string;
  data: string;
}

export interface ZoneData {
  name: string;
  records: ZoneRecord[];
}

export interface DNSZoneResponse {
  hostname: string;
  role: string;
  zones: ZoneData[];
}

// A core DNS server or CA on a router's customer network (GET /topology/router/{hostname}/attached)
export interface RouterAttached {
  hostname: string;
  type: string;
  category: 'dns' | 'ca';
  ip_address: string;
  subnet: string;
  role?: string;
  zones?: string[];
}

// DNS Record Management Types
export interface AddDNSRecordRequest {
  zone: string;
  name: string;
  type: string;
  data: string;
  ttl: number;
}

export interface AddDNSRecordResult {
  success: boolean;
  message: string;
  warnings: string[];
}

export interface CreateDomainRequest {
  domain: string;
  hosting_server?: string;
  ip_address?: string;
  mx_records?: string;
  additional_records?: { name: string; type: string; data: string; ttl?: number }[];
  create_www: boolean;
}

export interface CreateDomainResult {
  success: boolean;
  message: string;
  hosting_server?: string;
  tld_server?: string;
  records_created: number;
  warnings: string[];
}

// Presets: named sets of runtime changes on a base version (services/presets.py)
export interface PresetAction {
  action: string;
  target?: string | null;
  params: Record<string, unknown>;
}

export interface PresetOwner {
  kind: 'base';
  ref: string;
}

export interface Preset {
  name: string;
  description: string;
  owner: PresetOwner;
  created: string;
  created_by: string | null;
  actions: PresetAction[];
}

export interface PresetSummary {
  name: string;
  description: string;
  owner: PresetOwner;
  created: string;
  created_by: string | null;
  actions: number;
  kinds: Record<string, number>;
  problems: string[];
}

export interface ApplyPresetResult {
  success: boolean;
  message: string;
  applied: Record<string, number>;
  warnings: string[];
  errors: string[];
}

export interface TrackedChange {
  kind: string;
  key: string;
  summary: string;
  by: string | null;
  at: string;
}

/** GET /api/v1/changes: the active base preset and the changes since it */
export interface TrackedChanges {
  preset: string | null;
  core: string | null;
  count: number;
  changes: TrackedChange[];
}

// App Navigation
export interface CAServer {
  hostname: string;
  ca_name: string;
  role: string;
  ip_address: string;
  subnet: string;
  router: string;
  router_location: Location | null;
  router_isp: string;
  cores: number;
  memory: number;
  disk: number;
  deployed: boolean;
}

export interface Certificate {
  serial: string;
  subject: string;
  not_before: string;
  not_after: string;
  status: 'active' | 'revoked';
}

// Console Types
export interface ConsoleSessionInfo {
  session_id: string;
  hostname: string;
  created_at: string;
  last_activity: string;
  connected: boolean;
}
