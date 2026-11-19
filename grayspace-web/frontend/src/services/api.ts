/**
 * API service for backend communication
 */
import {
  Router,
  RouterDetail,
  Link,
  ISP,
  Container,
  InterfaceInfo,
  TrafficConfig,
  ZoneInfo,
  VNetInfo,
  LinkNetworkInfo,
  LinkTrafficConfig,
  CustomerInterfaceInfo,
  ConsoleSessionInfo,
  RouterLiveState,
  RouterInterfaceStats,
  LinkTrafficRates,
  DNSServer,
  CAServer,
  Certificate,
  RouterAttached,
  DNSZoneResponse,
  AddDNSRecordRequest,
  AddDNSRecordResult,
  CreateDomainRequest,
  CreateDomainResult,
  Preset,
  PresetSummary,
  ApplyPresetResult,
  TrackedChanges
} from '../types';
import {
  FeatureCollection,
  SliceSummary,
  SliceDetail,
  SliceSpec,
  PutSliceResult,
  ValidateResult,
  InstanceSummary,
  InstanceView,
  JobRecord,
  JobAccepted,
  JobFilter,
  DeployedBase,
  CatalogImage,
  CatalogRole,
  ActionRequest,
  ActionResult,
} from '../types/slices';
import { AppliedPlayerView, BaseVersion, Capabilities, DeployPlan, Draft, EnclaveSpec, PlayerCandidates, PlayerRole, PlayerTemplate, Preflight } from '../types/build';
import { authHeaders, reportUnauthorized, withToken } from './auth';

/** An error from the API, with its HTTP status and FastAPI detail. */
export class APIError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

const describe = (detail: unknown, fallback: string): string => {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message);
  if (Array.isArray(detail)) return detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? d.msg : String(d))).join('; ');
  return fallback;
};

// Use empty string for relative URLs (same origin) or set REACT_APP_API_URL for different host
export const API_BASE_URL = process.env.REACT_APP_API_URL !== undefined ? process.env.REACT_APP_API_URL : '';

/** The server's websocket origin: the API's host, or this page's. The development server claims
 * /ws for its own hot reload, so in development REACT_APP_WS_URL points at the backend directly. */
export const websocketOrigin = (): string => {
  if (process.env.REACT_APP_WS_URL) return process.env.REACT_APP_WS_URL.replace(/\/$/, '');
  if (API_BASE_URL) return API_BASE_URL.replace(/^http/, 'ws');
  return `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;
};

/** The open deployment's base path: /deployments/{id} (docs/deployments.md). */
let deploymentPrefix = '';

export const deploymentPath = (deploymentId: string): string => `/deployments/${encodeURIComponent(deploymentId)}`;

/** Where a deployment's websockets connect (/ws, /ws/console, /ws/desktop, /api/v1/events). */
export const websocketBase = (): string => `${websocketOrigin()}${deploymentPrefix}`;

/**
 * The open deployment's API. Every path is the deployment's, under /deployments/{id}; the
 * deployment shell sets it (setDeployment) before anything in it mounts, and remounts on a switch.
 */
class APIService {
  private get baseUrl(): string {
    return `${API_BASE_URL}${deploymentPrefix}`;
  }

  setDeployment(deploymentId: string | null): void {
    deploymentPrefix = deploymentId ? deploymentPath(deploymentId) : '';
  }

  get deployment(): string | null {
    return deploymentPrefix ? decodeURIComponent(deploymentPrefix.split('/')[2]) : null;
  }

  private async fetchJSON<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const response = await this.request(endpoint, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });
    if (response.status === 204) {
      return undefined as T;
    }
    return response.json();
  }

  /** fetch with the API token; throws APIError for non-2xx responses and reports 401s. */
  private async request(endpoint: string, options?: RequestInit): Promise<Response> {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      ...options,
      headers: { ...authHeaders(), ...options?.headers },
    });
    if (!response.ok) {
      if (response.status === 401) reportUnauthorized();
      let detail: unknown = response.statusText;
      try {
        detail = (await response.json()).detail;
      } catch {
        // not JSON
      }
      throw new APIError(response.status, describe(detail, `API request failed: ${response.status} ${response.statusText}`), detail);
    }
    return response;
  }

  // Topology endpoints
  async getRouters(): Promise<Router[]> {
    return this.fetchJSON<Router[]>('/api/v1/topology/routers');
  }

  async getLinks(): Promise<Link[]> {
    return this.fetchJSON<Link[]>('/api/v1/topology/links');
  }

  async getISPs(): Promise<ISP[]> {
    return this.fetchJSON<ISP[]>('/api/v1/topology/isps');
  }

  async getDNSServers(): Promise<DNSServer[]> {
    return this.fetchJSON<DNSServer[]>('/api/v1/topology/dns-servers');
  }

  async getCAServers(): Promise<CAServer[]> {
    return this.fetchJSON<CAServer[]>('/api/v1/ca/servers');
  }

  async getCACertificates(hostname: string): Promise<{ hostname: string; certificates: Certificate[] }> {
    return this.fetchJSON<{ hostname: string; certificates: Certificate[] }>(`/api/v1/ca/${hostname}/certificates`);
  }

  async issueCertificate(domain: string, caHostname?: string): Promise<{ success: boolean; message: string; certificate?: string; private_key?: string; ca_hostname?: string }> {
    return this.fetchJSON('/api/v1/ca/certificates/issue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain, ca_hostname: caHostname }),
    });
  }

  async revokeCertificate(hostname: string, serial: string, reason?: string): Promise<{ success: boolean; message: string }> {
    return this.fetchJSON(`/api/v1/ca/${hostname}/certificates/revoke`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ serial, reason }),
    });
  }

  async getRouterAttached(hostname: string): Promise<RouterAttached[]> {
    return this.fetchJSON<RouterAttached[]>(`/api/v1/topology/router/${hostname}/attached`);
  }

  async getDNSZones(hostname: string): Promise<DNSZoneResponse> {
    return this.fetchJSON<DNSZoneResponse>(`/api/v1/dns/${hostname}/zones`);
  }

  async getRouterDetail(hostname: string): Promise<RouterDetail> {
    return this.fetchJSON<RouterDetail>(`/api/v1/topology/router/${hostname}`);
  }

  async getRouterState(hostname: string): Promise<RouterLiveState> {
    return this.fetchJSON<RouterLiveState>(`/api/v1/topology/router/${hostname}/state`);
  }

  async getRouterInterfaceStats(hostname: string): Promise<RouterInterfaceStats> {
    return this.fetchJSON<RouterInterfaceStats>(`/api/v1/topology/router/${hostname}/interface-stats`);
  }

  // Container management endpoints
  async getContainers(): Promise<Container[]> {
    return this.fetchJSON<Container[]>('/api/v1/containers');
  }

  async getContainer(hostname: string): Promise<Container> {
    return this.fetchJSON<Container>(`/api/v1/containers/${hostname}`);
  }

  async startContainer(hostname: string): Promise<{ status: string; message: string }> {
    return this.fetchJSON(`/api/v1/containers/${hostname}/start`, {
      method: 'POST',
    });
  }

  async stopContainer(hostname: string): Promise<{ status: string; message: string }> {
    return this.fetchJSON(`/api/v1/containers/${hostname}/stop`, {
      method: 'POST',
    });
  }

  async restartContainer(hostname: string): Promise<{ status: string; message: string }> {
    return this.fetchJSON(`/api/v1/containers/${hostname}/restart`, {
      method: 'POST',
    });
  }

  // WebSocket connection
  createWebSocket(): WebSocket {
    return new WebSocket(withToken(`${websocketBase()}/ws`));
  }

  // Traffic shaping endpoints
  async getInterfaces(hostname: string): Promise<InterfaceInfo[]> {
    return this.fetchJSON<InterfaceInfo[]>(`/api/v1/traffic/${hostname}/interfaces`);
  }

  async getInterface(hostname: string, interfaceName: string): Promise<InterfaceInfo> {
    return this.fetchJSON<InterfaceInfo>(`/api/v1/traffic/${hostname}/interface/${interfaceName}`);
  }

  async applyTrafficConfig(
    hostname: string,
    interfaceName: string,
    config: Partial<TrafficConfig>
  ): Promise<{ status: string; message: string }> {
    return this.fetchJSON(`/api/v1/traffic/${hostname}/interface/${interfaceName}`, {
      method: 'PUT',
      body: JSON.stringify(config),
    });
  }

  async clearTrafficConfig(
    hostname: string,
    interfaceName: string
  ): Promise<{ status: string; message: string }> {
    return this.fetchJSON(`/api/v1/traffic/${hostname}/interface/${interfaceName}`, {
      method: 'DELETE',
    });
  }

  // Network (vNET/Zone) endpoints
  async getZones(): Promise<ZoneInfo[]> {
    return this.fetchJSON<ZoneInfo[]>('/api/v1/networks/zones');
  }

  async getVNets(): Promise<VNetInfo[]> {
    return this.fetchJSON<VNetInfo[]>('/api/v1/networks/vnets');
  }

  async changeVNetZone(
    vnetId: string,
    zoneId: string
  ): Promise<{ success: boolean; message: string; warnings: string[] }> {
    return this.fetchJSON(`/api/v1/networks/vnet/${vnetId}/zone`, {
      method: 'PUT',
      body: JSON.stringify({ zone_id: zoneId }),
    });
  }

  async getLinkNetworkInfo(linkId: string): Promise<LinkNetworkInfo> {
    return this.fetchJSON<LinkNetworkInfo>(`/api/v1/networks/link/${linkId}`);
  }

  // Link traffic shaping endpoints (symmetrical application)
  async getLinkTrafficRates(linkId: string): Promise<LinkTrafficRates> {
    return this.fetchJSON<LinkTrafficRates>(`/api/v1/links/${linkId}/rates`);
  }

  async getLinkTrafficConfig(linkId: string): Promise<LinkTrafficConfig> {
    return this.fetchJSON<LinkTrafficConfig>(`/api/v1/links/${linkId}/traffic`);
  }

  async applyLinkTrafficConfig(
    linkId: string,
    config: Partial<TrafficConfig>
  ): Promise<{ status: string; message: string }> {
    return this.fetchJSON(`/api/v1/links/${linkId}/traffic`, {
      method: 'PUT',
      body: JSON.stringify(config),
    });
  }

  async clearLinkTrafficConfig(linkId: string): Promise<{ status: string; message: string }> {
    return this.fetchJSON(`/api/v1/links/${linkId}/traffic`, {
      method: 'DELETE',
    });
  }

  // Customer interface zone endpoints
  async getCustomerInterfaces(hostname: string): Promise<CustomerInterfaceInfo[]> {
    return this.fetchJSON<CustomerInterfaceInfo[]>(`/api/v1/router/${hostname}/customer-interfaces`);
  }

  async getInterfaceZoneInfo(hostname: string, interfaceName: string): Promise<CustomerInterfaceInfo> {
    return this.fetchJSON<CustomerInterfaceInfo>(`/api/v1/router/${hostname}/interface/${interfaceName}/zone`);
  }

  async changeInterfaceZone(
    hostname: string,
    interfaceName: string,
    zoneId: string,
    vlanTag?: number
  ): Promise<{ success: boolean; message: string; warnings: string[] }> {
    const body: { zone_id: string; vlan_tag?: number } = { zone_id: zoneId };
    if (vlanTag !== undefined) {
      body.vlan_tag = vlanTag;
    }
    return this.fetchJSON(`/api/v1/router/${hostname}/interface/${interfaceName}/zone`, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }

  // Console endpoints
  async getConsoleSessions(): Promise<{ active_sessions: number; sessions: ConsoleSessionInfo[] }> {
    return this.fetchJSON('/api/v1/console/sessions');
  }

  async closeConsoleSession(sessionId: string): Promise<{ status: string; message: string }> {
    return this.fetchJSON(`/api/v1/console/session/${sessionId}`, {
      method: 'DELETE',
    });
  }

  // DNS record management endpoints
  async addDNSRecord(hostname: string, record: AddDNSRecordRequest): Promise<AddDNSRecordResult> {
    return this.fetchJSON<AddDNSRecordResult>(`/api/v1/dns/${hostname}/records`, {
      method: 'POST',
      body: JSON.stringify(record),
    });
  }

  async createDomain(request: CreateDomainRequest): Promise<CreateDomainResult> {
    return this.fetchJSON<CreateDomainResult>('/api/v1/dns/domains', {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  // Runtime changes and presets (routers/changes.py, routers/presets.py)
  async getChanges(): Promise<TrackedChanges> {
    return this.fetchJSON<TrackedChanges>('/api/v1/changes');
  }

  /** Reset changes: back to the active base preset */
  async discardChanges(): Promise<ApplyPresetResult> {
    return this.fetchJSON<ApplyPresetResult>('/api/v1/changes/discard', { method: 'POST' });
  }

  /** The base version's presets: the deployed one's, or base's */
  async getBasePresets(base?: string): Promise<PresetSummary[]> {
    return this.fetchJSON<PresetSummary[]>(`/api/v1/base/presets${base ? `?base=${encodeURIComponent(base)}` : ''}`);
  }

  async getBasePreset(name: string): Promise<Preset> {
    return this.fetchJSON<Preset>(`/api/v1/base/presets/${encodeURIComponent(name)}`);
  }

  /** The tracked changes as a preset of the deployed base version; it becomes the active preset */
  async saveBasePreset(name: string, description: string = ''): Promise<Preset> {
    return this.fetchJSON<Preset>('/api/v1/base/presets', { method: 'POST', body: JSON.stringify({ name, description }) });
  }

  async putBasePreset(preset: Preset): Promise<Preset> {
    return this.fetchJSON<Preset>(`/api/v1/base/presets/${encodeURIComponent(preset.name)}`, { method: 'PUT', body: JSON.stringify(preset) });
  }

  async deleteBasePreset(name: string): Promise<void> {
    await this.fetchJSON(`/api/v1/base/presets/${encodeURIComponent(name)}`, { method: 'DELETE' });
  }

  /** Reset changes, then the preset; null: none, the base as deployed */
  async applyBasePreset(name: string | null): Promise<ApplyPresetResult> {
    return this.fetchJSON<ApplyPresetResult>(`/api/v1/base/presets/${encodeURIComponent(name ?? 'none')}/apply`, { method: 'POST' });
  }


  createConsoleWebSocket(hostname: string, cols: number = 80, rows: number = 24): WebSocket {
    return new WebSocket(withToken(`${websocketBase()}/ws/console/${hostname}?cols=${cols}&rows=${rows}`));
  }

  // --- Terrain API: map layer, slices, instances, jobs, images ---

  /** A shipped base version's map as it would deploy, without state (GET /base/preview) */
  async getBasePreview(version: string, scope: string): Promise<FeatureCollection> {
    return this.fetchJSON<FeatureCollection>(`/api/v1/base/preview?version=${encodeURIComponent(version)}&scope=${scope}`);
  }

  async getMap(instance?: string): Promise<FeatureCollection> {
    return this.fetchJSON<FeatureCollection>(`/api/v1/map${instance ? `?instance=${encodeURIComponent(instance)}` : ''}`);
  }

  async getSlices(): Promise<SliceSummary[]> {
    return this.fetchJSON<SliceSummary[]>('/api/v1/enclaves');
  }

  async getSlice(slug: string, version?: string): Promise<SliceDetail> {
    return this.fetchJSON<SliceDetail>(`/api/v1/enclaves/${slug}${version ? `?version=${encodeURIComponent(version)}` : ''}`);
  }

  async putSlice(spec: SliceSpec): Promise<PutSliceResult> {
    return this.fetchJSON<PutSliceResult>(`/api/v1/enclaves/${spec.slug}`, { method: 'PUT', body: JSON.stringify(spec) });
  }

  async deleteSlice(slug: string): Promise<void> {
    return this.fetchJSON<void>(`/api/v1/enclaves/${slug}`, { method: 'DELETE' });
  }

  /** Findings against the deployed base; with base, against that shipped version (a deployment being built). */
  /** instance: check it as the draft's instance with this prefix (the one built from it is its own, not a conflict) */
  async validateSlice(spec: SliceSpec | EnclaveSpec, base?: { version: string; scope: string } | null, instance?: string): Promise<ValidateResult> {
    const params = new URLSearchParams();
    if (base) {
      params.set('base', base.version);
      params.set('scope', base.scope);
    }
    if (instance) params.set('instance', instance);
    const query = params.toString() ? `?${params}` : '';
    return this.fetchJSON<ValidateResult>(`/api/v1/enclaves/${spec.slug}/validate${query}`, { method: 'POST', body: JSON.stringify(spec) });
  }

  async putEnclave(spec: EnclaveSpec): Promise<PutSliceResult> {
    return this.fetchJSON<PutSliceResult>(`/api/v1/enclaves/${spec.slug}`, { method: 'PUT', body: JSON.stringify(spec) });
  }

  // The builder: base versions, preflight, capabilities, the draft and the rollout
  async getBaseVersions(): Promise<BaseVersion[]> {
    return this.fetchJSON<BaseVersion[]>('/api/v1/base/versions');
  }

  async getPreflight(version: string, scope: string): Promise<Preflight> {
    return this.fetchJSON<Preflight>(`/api/v1/base/preflight?version=${encodeURIComponent(version)}&scope=${scope}`);
  }

  async getCapabilities(version?: string, scope?: string): Promise<Capabilities> {
    const query = version ? `?version=${encodeURIComponent(version)}&scope=${scope || 'full'}` : '';
    return this.fetchJSON<Capabilities>(`/api/v1/capabilities${query}`);
  }

  async getDraft(): Promise<Draft> {
    return this.fetchJSON<Draft>('/api/v1/draft');
  }

  /** player: left out keeps no view; null removes it */
  async putDraft(draft: Pick<Draft, 'base' | 'enclaves' | 'player'>): Promise<Draft> {
    const body: Partial<Draft> = { base: draft.base, enclaves: draft.enclaves };
    if (draft.player !== undefined) body.player = draft.player;
    return this.fetchJSON<Draft>('/api/v1/draft', { method: 'PUT', body: JSON.stringify(body) });
  }

  // The Player step (routers/player.py): what a view can use
  async getPlayerTemplates(): Promise<PlayerTemplate[]> {
    return this.fetchJSON<PlayerTemplate[]>('/api/v1/player/templates');
  }

  async getPlayerView(): Promise<AppliedPlayerView> {
    return this.fetchJSON<AppliedPlayerView>('/api/v1/player');
  }

  async getPlayerRoles(): Promise<PlayerRole[]> {
    return this.fetchJSON<PlayerRole[]>('/api/v1/player/roles');
  }

  async getPlayerGuests(version?: string, scope?: string): Promise<PlayerCandidates> {
    const params = new URLSearchParams(Object.entries({ version, scope }).filter(([, v]) => v) as [string, string][]);
    return this.fetchJSON<PlayerCandidates>(`/api/v1/player/guests${params.toString() ? `?${params}` : ''}`);
  }

  async getDeployPlan(): Promise<DeployPlan> {
    return this.fetchJSON<DeployPlan>('/api/v1/deploy/plan');
  }

  async deploy(): Promise<JobAccepted> {
    return this.fetchJSON<JobAccepted>('/api/v1/deploy', { method: 'POST' });
  }

  /** The CA bundle (PEM), for a download. */
  async getCABundle(): Promise<string> {
    return (await this.request('/api/v1/ca/bundle')).text();
  }

  async planSlice(slug: string, version?: string): Promise<JobAccepted> {
    return this.fetchJSON<JobAccepted>(`/api/v1/enclaves/${slug}/plan`, { method: 'POST', body: JSON.stringify({ version }) });
  }

  async getInstances(all = false): Promise<InstanceSummary[]> {
    return this.fetchJSON<InstanceSummary[]>(`/api/v1/instances${all ? '?all=true' : ''}`);
  }

  async getInstance(id: string): Promise<InstanceView> {
    return this.fetchJSON<InstanceView>(`/api/v1/instances/${id}`);
  }

  async createInstance(slice: string, version?: string, prefix?: string): Promise<JobAccepted> {
    return this.fetchJSON<JobAccepted>('/api/v1/instances', { method: 'POST', body: JSON.stringify({ enclave: slice, version, prefix: prefix || undefined }) });
  }

  async destroyInstance(id: string): Promise<JobAccepted> {
    return this.fetchJSON<JobAccepted>(`/api/v1/instances/${id}`, { method: 'DELETE' });
  }

  async instanceAction(id: string, action: ActionRequest): Promise<ActionResult> {
    return this.fetchJSON<ActionResult>(`/api/v1/instances/${id}/actions`, { method: 'POST', body: JSON.stringify(action) });
  }

  async getJobs(filter: string | JobFilter = {}): Promise<JobRecord[]> {
    const f: JobFilter = typeof filter === 'string' ? { target: filter } : filter;
    const params = new URLSearchParams();
    if (f.target) params.set('target', f.target);
    if (f.parent) params.set('parent', f.parent);
    if (f.limit) params.set('limit', String(f.limit));
    (f.kind || []).forEach((k) => params.append('kind', k));
    (f.state || []).forEach((s) => params.append('state', s));
    const query = params.toString();
    return this.fetchJSON<JobRecord[]>(`/api/v1/jobs${query ? `?${query}` : ''}`);
  }

  // The base
  async getBase(): Promise<DeployedBase> {
    return this.fetchJSON<DeployedBase>('/api/v1/base');
  }

  async configureBase(): Promise<JobAccepted> {
    return this.fetchJSON<JobAccepted>('/api/v1/base/configure', { method: 'POST', body: '{}' });
  }

  /** Destroy the base; with destroyInstances, every live instance's destroy job is queued first. */
  async destroyBase(destroyInstances: boolean): Promise<JobRecord[]> {
    return this.fetchJSON<JobRecord[]>(`/api/v1/base?destroy_instances=${destroyInstances}`, { method: 'DELETE' });
  }

  async getJob(id: string): Promise<JobRecord> {
    return this.fetchJSON<JobRecord>(`/api/v1/jobs/${id}`);
  }

  async cancelJob(id: string): Promise<JobRecord> {
    return this.fetchJSON<JobRecord>(`/api/v1/jobs/${id}/cancel`, { method: 'POST' });
  }

  async retryJob(id: string): Promise<JobAccepted> {
    return this.fetchJSON<JobAccepted>(`/api/v1/jobs/${id}/retry`, { method: 'POST' });
  }

  /** Stream a job's log until it finishes (or the signal aborts). */
  async streamJobLog(id: string, onText: (text: string) => void, signal: AbortSignal): Promise<void> {
    const response = await this.request(`/api/v1/jobs/${id}/log?follow=true`, { signal });
    const reader = response.body?.getReader();
    if (!reader) {
      onText(await response.text());
      return;
    }
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      onText(decoder.decode(value, { stream: true }));
    }
  }

  async getImages(): Promise<CatalogImage[]> {
    return this.fetchJSON<CatalogImage[]>('/api/v1/images');
  }

  async getRoles(): Promise<CatalogRole[]> {
    return this.fetchJSON<CatalogRole[]>('/api/v1/roles');
  }

  /** Terrain API events: map feature updates, job and instance progress (/api/v1/events). */
  createEventsWebSocket(): WebSocket {
    return new WebSocket(withToken(`${websocketBase()}/api/v1/events`));
  }
}

export const apiService = new APIService();
