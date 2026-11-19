/** The builder's resources: base versions, preflight, capabilities, the draft and the deploy plan (openapi/grayspace-v1.json). */
import { Finding } from './slices';

export interface BaseVersion {
  name: string;
  csv_sha256: string | null;
  deployed: boolean;
  summary: { isps: number; locations: number; routers: number; links: number; core_services: number; /** Deprecated (contract 1.18): base clients are retired; always 0 */ clients?: number } | null;
  problems: string[];
}

export interface PreflightCheck {
  name: string;
  status: 'ok' | 'warning' | 'error' | 'unchecked';
  message: string;
}

export interface Preflight {
  ready: boolean;
  version: string | null;
  scope: string | null;
  checks: PreflightCheck[];
}

export interface Capacity {
  node?: string;
  cpus: number | null;
  cpu_usage: number | null;
  memory_total: number | null;
  memory_free: number | null;
  storage?: string | null;
  storage_total?: number | null;
  storage_free?: number | null;
}

/** A role's variable (ansible/roles/<name>/meta/grayspace.yml), set in the spec's role_vars. */
export interface RoleVariable { description: string; required?: boolean; default?: unknown }
export interface Role { name: string; description: string; os_families: string[]; image_types?: ('lxc' | 'vm')[] | null; variables?: Record<string, RoleVariable> }

export interface Capabilities {
  base: { name: string | null; csv_sha256: string; scope: string };
  images: { name: string; type: string; os_family: string; description: string }[];
  roles: Role[];
  dns: { tlds: string[]; hosting: { hostname: string; isp: string }[]; resolvers: { hostname: string }[] };
  cas: { hostname: string; name: string; isp: string }[];
  isps: { name: string; asn: number | null; locations: string[] }[];
  capacity: Capacity | null;
  jobs: boolean;
}

export type Scope = 'full' | 'tier1';

export interface DraftBase {
  version: string;
  scope: Scope;
  configure: boolean;
  /** A base preset of the version, applied after the base; null leaves runtime changes as they are */
  preset?: string | null;
}

export interface DraftEnclave {
  slug: string;
  version?: string | null;
  prefix: string;
}

/** The deployment's Crucible Player view (the draft's player section, docs/deployments.md). */
export interface PlayerApplication {
  name: string;
  template_id?: string | null;
  url?: string | null;
  icon?: string | null;
  embeddable?: boolean;
  load_in_background?: boolean;
}

export interface PlayerTeam {
  name: string;
  role?: string | null;
  permissions?: string[];
  /** The view's applications the team gets, in display order */
  applications: string[];
}

export interface PlayerGuest {
  /** An enclave instance of the draft; null for a base guest */
  instance?: string | null;
  /** A base hostname, or an enclave host or site as its spec names it */
  name: string;
  site?: boolean;
  /** Empty: not in the view, even when the spec marks it crucible: true */
  teams: string[];
}

export interface DraftPlayer {
  view_name?: string | null;
  description?: string;
  applications: PlayerApplication[];
  teams: PlayerTeam[];
  default_team?: string | null;
  /** Teams for every guest of the base (scope base) or of an enclave instance (enclave:<prefix>) */
  assign?: PlayerRule[];
  /** Guests whose teams differ from what assign gives them */
  guests: PlayerGuest[];
}

export interface PlayerRule { scope: string; teams: string[] }

/** GET /api/v1/player/guests */
export interface PlayerCandidates {
  base: { hostname: string; group: 'routers' | 'core_services' | 'management'; isp?: string | null; role?: string; type?: string }[];
  enclaves: { prefix: string; slug: string; sites: { name: string; edge: string; hint: boolean }[]; hosts: { name: string; count: number; image: string; hint: boolean }[] }[];
}

/** GET /player: the view the rollout applied, if any, and its page in Player */
export interface AppliedPlayerView { view: string | null; view_id: string | null; url: string | null; applied?: string | null }
export interface PlayerTemplate { id: string; name: string; url?: string | null; icon?: string | null; embeddable?: boolean | null }
export interface PlayerRole { id: string; name: string }

export interface Draft {
  base: DraftBase | null;
  enclaves: DraftEnclave[];
  player?: DraftPlayer | null;
  updated?: string | null;
  updated_by?: string | null;
  adopted?: boolean;
}

export interface PlannedInstance {
  id: string;
  slug: string;
  version: string | null;
  action: 'create' | 'destroy' | 'replace' | 'keep';
  state: string | null;
}

export interface DeployPlan {
  base: { action: 'none' | 'deploy' | 'update'; deployed: { name: string | null; scope: string } | null; version: string | null; scope: string | null; configure: boolean;
    preset?: string | null; active_preset?: string | null };
  instances: PlannedInstance[];
  player?: { action: 'none' | 'create' | 'update' | 'destroy'; view?: string | null; teams: number; guests: number; warnings: string[] } | null;
  unmanaged: string[];
  problems: string[];
  stages: string[];
  changes: boolean;
  capacity: Capacity | null;
  /** The last rollout when retrying it would still deploy this draft (contract 1.22) */
  resumable_rollout?: string | null;
}

/** The enclave spec as the builder edits it (openapi/grayspace-enclave-v1.json); unknown fields pass through. */
export interface Attach { isp?: string; location?: string; router?: string }
export interface Segment { name: string; cidr?: string }
export interface Site { name: string; attach: Attach | Attach[]; routed?: boolean; segments: Segment[]; crucible?: boolean; [k: string]: unknown }
export interface Host {
  name: string; count?: number; segment?: string; internet?: Attach; image: string; roles?: string[]; crucible?: boolean; [k: string]: unknown;
}
export interface DnsDomain { domain: string; hosting_server: string }
export interface DnsRecord { name: string; type?: 'A' | 'CNAME' | 'TXT' | 'MX'; host?: string; data?: string; ttl?: number }
export interface Tls { host: string; names: string[]; ca: string }
export interface EnclaveSpec {
  apiVersion: 'grayspace/v1';
  kind: 'Slice' | 'Enclave';
  slug: string;
  prefix: string;
  description?: string;
  owner?: string | null;
  sites?: Site[];
  hosts?: Host[];
  dns?: { domains?: DnsDomain[]; records?: DnsRecord[] };
  tls?: Tls[];
  role_vars?: Record<string, Record<string, unknown>>;
  [k: string]: unknown;
}

export interface Validation {
  valid: boolean;
  findings: Finding[];
  checking?: boolean;
}
