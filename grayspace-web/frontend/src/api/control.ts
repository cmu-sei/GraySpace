/**
 * The control plane's API, at the server's root: sign-in, who you are, deployments, users
 * (docs/deployments.md). A deployment's own API is services/api.ts, under /deployments/{id}.
 */
import { API_BASE_URL, APIError, deploymentPath, websocketOrigin } from '../services/api';
import { CatalogImage, CatalogRole } from '../types/slices';
import { authHeaders, reportUnauthorized, withToken } from '../services/auth';
import {
  AuditEntry, ConnectionTest, CreateDeployment, CreateUser, CrucibleConnectionWrite, Deployment, DirectoryEntry, Discovery, LoginResponse, Me,
  Member, NodeInfo, OneTimePassword, Platform, ProxmoxConnectionWrite, SwitcherApp, SessionInfo, UpdateDeployment, User,
} from '../types/control';

const message = (detail: unknown, fallback: string): string => {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message);
  if (Array.isArray(detail)) return detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? d.msg : String(d))).join('; ');
  return fallback;
};

async function call<T>(path: string, options: RequestInit = {}, quiet401 = false): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...options.headers },
  });
  if (!response.ok) {
    if (response.status === 401 && !quiet401) reportUnauthorized();
    let detail: unknown = response.statusText;
    try {
      detail = (await response.json()).detail;
    } catch {
      // not JSON
    }
    throw new APIError(response.status, message(detail, `${response.status} ${response.statusText}`), detail);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

export const control = {
  providers: () => call<{ providers: string[] }>('/api/v1/auth/providers'),
  // The header's app switcher; a failure leaves it empty rather than signing out
  apps: () => call<SwitcherApp[]>('/api/apps', {}, true),
  // A wrong password is a 401 too; the sign-in page shows it rather than starting over
  login: (username: string, password: string) =>
    call<LoginResponse>('/api/v1/auth/login', { method: 'POST', ...json({ username, password }) }, true),
  logout: () => call<void>('/api/v1/auth/logout', { method: 'POST' }, true),
  me: () => call<Me>('/api/v1/me', {}, true),
  changePassword: (current_password: string, new_password: string) =>
    call<void>('/api/v1/auth/password', { method: 'POST', ...json({ current_password, new_password }) }),
  sessions: () => call<SessionInfo[]>('/api/v1/auth/sessions'),
  endSession: (id: string) => call<void>(`/api/v1/auth/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  deployments: () => call<Deployment[]>('/api/v1/deployments'),
  deployment: (id: string) => call<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}`),
  createDeployment: (body: CreateDeployment) => call<Deployment>('/api/v1/deployments', { method: 'POST', ...json(body) }),
  setMembers: (id: string, members: Member[]) =>
    call<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}/members`, { method: 'PUT', ...json({ members }) }),
  deploymentEvents: () => new WebSocket(withToken(`${websocketOrigin()}/api/v1/deployments/events`)),

  updateDeployment: (id: string, body: UpdateDeployment) =>
    call<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}`, { method: 'PATCH', ...json(body) }),
  deleteDeployment: (id: string) => call<void>(`/api/v1/deployments/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  restartDeployment: (id: string) => call<Deployment>(`/api/v1/deployments/${encodeURIComponent(id)}/restart`, { method: 'POST' }),
  directory: () => call<DirectoryEntry[]>('/api/v1/directory'),

  users: () => call<User[]>('/api/v1/users'),
  createUser: (body: CreateUser) => call<OneTimePassword>('/api/v1/users', { method: 'POST', ...json(body) }),
  updateUser: (username: string, body: Partial<Pick<User, 'name' | 'email' | 'role' | 'disabled'>>) =>
    call<User>(`/api/v1/users/${encodeURIComponent(username)}`, { method: 'PATCH', ...json(body) }),
  deleteUser: (username: string) => call<void>(`/api/v1/users/${encodeURIComponent(username)}`, { method: 'DELETE' }),
  resetPassword: (username: string) => call<OneTimePassword>(`/api/v1/users/${encodeURIComponent(username)}/reset-password`, { method: 'POST' }),
  audit: (filter: { deployment?: string; user?: string; limit?: number } = {}) => {
    const params = new URLSearchParams(Object.entries(filter).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)]));
    return call<AuditEntry[]>(`/api/v1/audit${params.toString() ? `?${params}` : ''}`);
  },
  nodes: () => call<NodeInfo[]>('/api/v1/nodes'),

  // Platform connections (Admin > Platform); builders read them for the new-deployment form
  platform: () => call<Platform>('/api/v1/platform'),
  setDefaultProxmox: (id: string | null) => call<Platform>('/api/v1/platform/defaults', { method: 'PUT', ...json({ default_proxmox: id }) }),
  createProxmox: (body: ProxmoxConnectionWrite & { id: string }) => call<Platform>('/api/v1/platform/proxmox', { method: 'POST', ...json(body) }),
  updateProxmox: (id: string, body: ProxmoxConnectionWrite) =>
    call<{ restart_pending: string[] }>(`/api/v1/platform/proxmox/${encodeURIComponent(id)}`, { method: 'PATCH', ...json(body) }),
  deleteProxmox: (id: string) => call<void>(`/api/v1/platform/proxmox/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  testProxmox: (id: string) => call<ConnectionTest>(`/api/v1/platform/proxmox/${encodeURIComponent(id)}/test`, { method: 'POST' }),
  discoverProxmox: (id: string) => call<Discovery>(`/api/v1/platform/proxmox/${encodeURIComponent(id)}/discover`),
  createCrucible: (body: CrucibleConnectionWrite & { id: string }) => call<Platform>('/api/v1/platform/crucible', { method: 'POST', ...json(body) }),
  updateCrucible: (id: string, body: CrucibleConnectionWrite) =>
    call<{ restart_pending: string[] }>(`/api/v1/platform/crucible/${encodeURIComponent(id)}`, { method: 'PATCH', ...json(body) }),
  deleteCrucible: (id: string) => call<void>(`/api/v1/platform/crucible/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  testCrucible: (id: string) => call<ConnectionTest>(`/api/v1/platform/crucible/${encodeURIComponent(id)}/test`, { method: 'POST' }),

  // A deployment's catalog, from the admin area (outside the deployment's own views)
  images: (id: string) => call<CatalogImage[]>(`${deploymentPath(id)}/api/v1/images`),
  roles: (id: string) => call<CatalogRole[]>(`${deploymentPath(id)}/api/v1/roles`),
  deleteImage: (id: string, name: string) => call<void>(`${deploymentPath(id)}/api/v1/images/${encodeURIComponent(name)}`, { method: 'DELETE' }),
};
