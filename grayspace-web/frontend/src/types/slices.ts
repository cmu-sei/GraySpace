/**
 * Types of the Terrain API's map layer, slices, instances and jobs (openapi/grayspace-v1.json).
 */

// --- Map layer (GET /api/v1/map): GeoJSON, RFC 7946 ---

export type Position = [number, number]; // [longitude, latitude]

export type Geometry =
  | { type: 'Point'; coordinates: Position }
  | { type: 'LineString'; coordinates: Position[] }
  | { type: 'MultiLineString'; coordinates: Position[][] };

export type FeatureKind = 'router' | 'link' | 'site' | 'internet-host' | 'attachment';

export interface FeatureProperties {
  id: string;
  kind: FeatureKind;
  color?: string;
  // router
  isp?: string;
  asn?: number;
  location?: string;
  status?: string;
  // link
  type?: 'INTERNAL_LINK' | 'PEERS_WITH' | 'PROVIDES_TRANSIT_TO';
  state?: string;
  source?: string;
  target?: string;
  // site, internet-host, attachment
  instance?: string;
  name?: string;
  router?: string;
  routers?: string[];
  wan_ip?: string;
  routed?: boolean;
  edge?: string;
  hostname?: string;
  ip?: string;
  fqdns?: string[];
  from?: string;
  [key: string]: unknown;
}

export interface Feature {
  type: 'Feature';
  id: string;
  geometry: Geometry;
  properties: FeatureProperties;
}

export interface FeatureCollection {
  type: 'FeatureCollection';
  features: Feature[];
}

// --- Slices ---

export interface Finding {
  code: string;
  severity: 'error' | 'warning';
  path: string;
  message: string;
  suggestion?: unknown;
}

/** The slice spec (openapi/grayspace-slice-v1.json); the UI shows it but doesn't edit it. */
export interface SliceSpec {
  apiVersion: 'grayspace/v1';
  kind: 'Slice';
  slug: string;
  prefix: string;
  description?: string;
  owner?: string | null;
  sites?: unknown[];
  hosts?: unknown[];
  dns?: unknown;
  tls?: unknown[];
  conditions?: unknown;
  role_vars?: Record<string, Record<string, unknown>>;
}

export interface SliceVersion {
  version: string;
  created: string;
}

export interface SliceSummary {
  slug: string;
  kind: 'base' | 'slice';
  description: string;
  owner?: string | null;
  latest?: string | null;
  versions: SliceVersion[];
  instances: string[];
}

export interface SliceDetail extends SliceSummary {
  version?: string | null;
  spec?: SliceSpec | null;
  base?: { name: string | null; csv_sha256: string; scope: string } | null;
}

export interface PutSliceResult {
  slug: string;
  version: string;
  created: boolean;
  findings: Finding[];
}

export interface ValidateResult {
  valid: boolean;
  version: string;
  findings: Finding[];
}

// --- Instances and their manifests ---

export type InstanceState = 'pending' | 'provisioning' | 'configuring' | 'ready' | 'failed' | 'destroying' | 'destroyed';

export interface SliceRef {
  slug: string;
  version: string;
}

export interface InstanceSummary {
  id: string;
  /** Deprecated (contract 1.5): the same as enclave */
  slice: SliceRef;
  enclave?: SliceRef;
  state: InstanceState;
  created: string;
  updated: string;
  error: string | null;
  hosts: number;
  sites: number;
  last_job: string | null;
}

export interface Wan {
  router: string;
  isp: string;
  asn: number;
  location: string | null;
  customer_net: string;
  bridge: string;
  ip: string;
  gateway: string;
}

export interface ManifestSite {
  name: string;
  router: string;
  isp: string;
  location: string | null;
  wan_ip: string;
  routed: boolean;
  public_prefix: string | null;
  wans: Wan[];
  edge: string;
  segments: { name: string; cidr: string; gateway: string }[];
}

export interface ManifestHost {
  name: string;
  hostname: string;
  site: string | null;
  segment: string | null;
  ip: string;
  public_ip: string | null;
  mgmt_ip: string | null;
  fqdns: string[];
  roles: string[];
  labels: Record<string, string>;
  image: string;
  status: string;
}

export interface InstanceView {
  id: string;
  /** Deprecated (contract 1.5): the same as enclave */
  slice: SliceRef;
  enclave?: SliceRef;
  core: { name: string | null; csv_sha256: string; scope: string } | null;
  state: InstanceState;
  sites: ManifestSite[];
  hosts: ManifestHost[];
  dns: { name: string; type: string; data: string }[];
  certificates: { host: string; names: string[]; ca: string; serial: string | null }[];
  created: string;
  updated: string;
  error: string | null;
  stages_done: string[];
  jobs: string[];
}

// --- Jobs ---

export type JobState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface JobRecord {
  id: string;
  kind: 'create_instance' | 'destroy_instance' | 'plan_slice' | 'deploy_base' | 'configure_base' | 'destroy_base' | 'rollout';
  target: string;
  slice: SliceRef | null;
  state: JobState;
  stages: string[];
  stage: string | null;
  attempt: string | null;
  created: string;
  started: string | null;
  finished: string | null;
  error: string | null;
  cancel_requested: boolean;
  resume_from: string | null;
  retry_of: string | null;
  result: Record<string, unknown> | null;
  /** Who submitted it; null for the service token */
  by?: string | null;
  /** The rollout that ran it */
  parent?: string | null;
  /** A rollout's jobs, in order */
  children?: string[];
  /** What each stage does */
  stage_titles?: Record<string, string>;
}

export interface JobFilter {
  target?: string;
  kind?: string[];
  state?: string[];
  parent?: string;
  limit?: number;
}

/** GET /api/v1/base: the deployed version and its health */
export interface DeployedBase {
  version: { name: string | null; csv_sha256: string; scope: string } | null;
  status: 'not_deployed' | 'healthy' | 'degraded' | 'unknown';
  routers: { expected: number; running: number | null; not_running: string[] };
  bgp: { total: number | null; established: number | null; not_established: { router: string; peer_address: string; state: string }[] };
}

export interface JobAccepted {
  instance: string | null;
  job: JobRecord;
}

// --- Catalog and actions ---

export interface CatalogImage {
  name: string;
  type: 'lxc' | 'vm' | 'oci';
  os_family: string;
  source: string;
  cores: number;
  memory: number;
  disk: number;
  features: string[];
  description: string;
  origin: 'shipped' | 'local';
}

export interface CatalogRole {
  name: string;
  description: string;
  os_families: string[];
  image_types?: ('lxc' | 'vm')[] | null;
  variables: Record<string, { description: string; required: boolean; default?: unknown }>;
}

export interface ActionRequest {
  action: 'link.condition' | 'link.clear' | 'link.down' | 'link.up' | 'dns.record.set' | 'dns.record.delete' | 'host.start' | 'host.stop' | 'reset';
  target?: string;
  params?: Record<string, unknown>;
}

export interface ActionResult {
  action: string;
  target: string | null;
  message: string;
  instance: string;
}

/** A message on /api/v1/events (openapi/grayspace-events-v1.json). */
export type TerrainEvent =
  | { type: 'hello'; schema: string }
  | { type: 'pong' }
  | { type: 'feature.update'; id: string; kind: string; properties: Partial<FeatureProperties>; timestamp: string }
  | { type: 'job.update'; id: string; kind: string; target: string; state: JobState; stage: string | null; attempt: string | null; error: string | null; timestamp: string }
  | { type: 'instance.update'; id: string; state: InstanceState; error: string | null; timestamp: string }
  | { type: 'action.applied'; instance: string; action: string; target: string | null; message: string; timestamp: string };
