/**
 * The control plane's resources: sign-in, users, deployments, the audit log and nodes
 * (openapi/grayspace-v1.json, docs/deployments.md).
 */
export type Role = 'admin' | 'builder' | 'user';

export interface User {
  username: string;
  name: string;
  email: string;
  role: Role;
  source: 'local' | 'oidc';
  disabled: boolean;
  must_change_password: boolean;
  created?: string | null;
  last_seen?: string | null;
}

export interface Me {
  user: User | null;
  kind: 'user' | 'service';
  role: Role;
  permissions: string[];
  must_change_password: boolean;
  providers: string[];
}

export interface SessionInfo {
  id: string;
  created: string;
  last_used: number;
  expires: number;
  ip: string;
  agent: string;
  current: boolean;
}

/** An app in the header's app switcher (GET /api/apps); without a url it isn't shown. */
export interface SwitcherApp {
  id: string;
  name: string;
  url?: string;
}

export interface LoginResponse {
  token: string;
  session: SessionInfo;
  must_change_password: boolean;
}

export interface WorkerStatus {
  state: 'stopped' | 'starting' | 'running' | 'crashed';
  pid?: number | null;
  started?: string | null;
  restarts: number;
  error?: string | null;
}

export interface BaseSummary {
  status: string;
  version?: { name: string | null; csv_sha256: string; scope: string } | null;
  scope?: string | null;
  routers?: number | null;
}

export interface JobBrief {
  id: string;
  kind: string;
  state: string;
  stage?: string | null;
  attempt?: string | null;
  finished?: string | null;
}

export interface DeploymentSummary {
  instances_live: number;
  instances_failed: number;
  active_job?: JobBrief | null;
  last_core_job?: JobBrief | null;
  updated?: string | null;
  draft?: { saved: boolean; changes: boolean | null } | null;
}

export interface Member {
  user: string;
  access: 'build' | 'use';
}

export interface ProxmoxSettings {
  host_addr?: string | null;
  node?: string | null;
  blockstore?: string | null;
  filestore?: string | null;
  mgmt_bridge?: string | null;
  vm_bridge?: string | null;
}

/** Platform connections (Admin > Platform, GET /api/v1/platform): defined once, chosen by deployments. */
export interface ProxmoxNode {
  name: string;
  blockstore?: string | null;
  filestore?: string | null;
  mgmt_bridge?: string | null;
  vm_bridge?: string | null;
}

export interface ProxmoxConnection {
  id: string;
  name: string;
  host: string;
  port: number;
  verify_ssl: boolean;
  auth: 'token' | 'password';
  username?: string | null;
  ssh_username?: string | null;
  nodes: ProxmoxNode[];
  default_node: string;
  /** Terraform's -parallelism for applies and destroys (concurrent creates); unset: the server's default (10) */
  apply_parallelism?: number | null;
  /** Terraform's -parallelism for plans; unset: the server's default (30) */
  plan_parallelism?: number | null;
  /** The proxy its deployments' guests install packages through: site, the site file's (TF_VAR_proxy); none; custom, proxy and no_proxy */
  proxy_mode?: 'site' | 'none' | 'custom';
  proxy?: string | null;
  no_proxy?: string | null;
  secrets: string[];
  deployments: string[];
}

/** What a form sends: secrets are write-only (empty clears one; left out keeps it). */
export type ProxmoxConnectionWrite = Partial<Omit<ProxmoxConnection, 'secrets' | 'deployments'>> & {
  api_token?: string; password?: string; ssh_password?: string;
};

export interface CrucibleConnection {
  id: string;
  name: string;
  auth_url: string;
  token_url: string;
  client_id: string;
  client_scopes: string[];
  username: string;
  vm_api_url: string;
  player_api_url: string;
  caster_api_url: string;
  service_user_id: string;
  /** Player's UI, when it isn't the Player API URL without /api */
  player_url?: string;
  secrets: string[];
  deployments: string[];
}

export type CrucibleConnectionWrite = Partial<Omit<CrucibleConnection, 'secrets' | 'deployments'>> & { password?: string };

export interface Platform {
  proxmox: ProxmoxConnection[];
  crucible: CrucibleConnection[];
  default_proxmox: string | null;
  imported_from?: string | null;
}

export interface ConnectionTest {
  ok: boolean;
  checks: { name: string; status: 'ok' | 'warning' | 'error'; message: string }[];
}

export interface Discovery {
  version?: string | null;
  nodes: { name: string; status?: string | null; storages: { storage: string; type?: string; content?: string }[]; bridges: string[] }[];
  vnets: string[];
}

export interface Placement {
  connection: string;
  node?: string | null;
}

export interface DeploymentOptions {
  /** @deprecated Ignored since contract 1.25: the base has no GHOSTS VM */
  deploy_ghosts?: boolean | null;
  /** @deprecated Ignored since contract 1.12: every Alpine guest uses the GraySpace image */
  use_custom_image?: boolean | null;
  /** A Crucible Player view */
  player?: boolean | null;
  player_connection?: string | null;
}

export interface Deployment {
  id: string;
  name: string;
  description: string;
  kind: 'managed' | 'external';
  default: boolean;
  created: string;
  url: string;
  /** The Proxmox connection and node; null for an adopted tree that matches no connection */
  placement?: Placement | null;
  /** Where it runs, resolved from the placement */
  proxmox: ProxmoxSettings;
  options: DeploymentOptions;
  secrets: string[];
  worker: WorkerStatus;
  core?: BaseSummary | null;
  summary?: DeploymentSummary | null;
  owner?: string | null;
  members: Member[];
  my_access: string[];
  warnings: string[];
}

export interface CreateDeployment {
  id: string;
  name?: string;
  description?: string;
  placement?: Placement;
  options?: DeploymentOptions;
  router_password?: string;
}

export interface DeploymentEvent {
  type: 'hello' | 'pong' | 'deployment.created' | 'deployment.updated' | 'deployment.deleted' | 'deployment.worker';
  id?: string;
  deployment?: Deployment | null;
}

/** What the switcher and the home page say about a deployment. */
export type DeploymentState = 'none' | 'draft' | 'deploying' | 'live' | 'degraded' | 'failed' | 'down';

export interface DirectoryEntry {
  username: string;
  name: string;
  role: Role;
}

export interface OneTimePassword {
  user: User;
  one_time_password: string;
}

export interface CreateUser {
  username: string;
  name?: string;
  email?: string;
  role: Role;
  deployments?: string[];
}

export interface AuditEntry {
  at: string;
  user: string | null;
  method: string;
  path: string;
  deployment: string | null;
  status: number;
  detail?: string;
  ip?: string;
}

export interface NodeInfo {
  connection?: string | null;
  connection_name?: string | null;
  host: string | null;
  node: string | null;
  deployments: string[];
  capacity: {
    cpus: number | null; cpu_usage: number | null; memory_total: number | null; memory_free: number | null;
    storage?: string | null; storage_total?: number | null; storage_free?: number | null;
  } | null;
  locked: boolean;
  lock_holder: string | null;
}

export interface UpdateDeployment {
  name?: string;
  description?: string;
  default?: boolean;
  placement?: Placement;
  options?: DeploymentOptions;
}
