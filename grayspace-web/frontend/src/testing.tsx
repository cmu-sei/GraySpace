/** Helpers for component tests: a fake server for fetch, and sample records. */
import { Deployment, Me } from './types/control';

type Handler = (url: string, init?: RequestInit) => { status?: number; body?: unknown } | undefined;

/** fetch answered by the handler; unmatched requests are 404s. Returns the calls made. */
export const fakeServer = (handler: Handler) => {
  const calls: { url: string; init?: RequestInit }[] = [];
  global.fetch = jest.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const answer = handler(url, init) || { status: 404, body: { detail: 'Not found' } };
    const status = answer.status ?? 200;
    return {
      ok: status < 400, status, statusText: String(status),
      json: async () => answer.body,
      text: async () => (typeof answer.body === 'string' ? answer.body : JSON.stringify(answer.body)),
    } as Response;
  }) as typeof fetch;
  return calls;
};

/** A websocket that never connects, so providers that open one stay quiet. */
export class SilentWebSocket {
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  close() {}
  send() {}
}

export const me = (role: Me['role'], username: string = role, extra: Partial<Me> = {}): Me => ({
  user: { username, name: username, email: '', role, source: 'local', disabled: false, must_change_password: false },
  kind: 'user', role, permissions: role === 'admin' ? ['deployments:create', 'deployments:all', 'users:manage', 'keys:manage', 'catalog:write', 'nodes:read', 'audit:read', 'platform:manage'] : role === 'builder' ? ['deployments:create'] : [],
  must_change_password: false, providers: ['local'], ...extra,
});

/** A platform with one Proxmox connection of two nodes and one Crucible connection. */
export const PLATFORM = {
  proxmox: [{ id: 'site', name: 'Site', host: 'pve1.example', port: 8006, verify_ssl: false, auth: 'token', username: null, ssh_username: 'root',
    nodes: [{ name: 'pve1', blockstore: 'local-lvm', vm_bridge: 'vmbr1' }, { name: 'pve2' }], default_node: 'pve1', secrets: ['api_token'], deployments: ['ex1'] }],
  crucible: [{ id: 'cru', name: 'Crucible', auth_url: '', token_url: 'https://c/token', client_id: 'tf', client_scopes: ['player-api'], username: 'svc',
    vm_api_url: '', player_api_url: 'https://c/player/api', caster_api_url: '', service_user_id: 'u1', secrets: ['password'], deployments: [] }],
  default_proxmox: 'site', imported_from: '/data/.environment',
};

export const deployment = (id: string, extra: Partial<Deployment> = {}): Deployment => ({
  id, name: id, description: '', kind: 'managed', default: false, created: '2026-11-10T00:00:00+00:00', url: `/deployments/${id}`,
  proxmox: {}, options: {}, secrets: [], worker: { state: 'running', restarts: 0 },
  base: { status: 'not_deployed' }, summary: { instances_live: 0, instances_failed: 0 }, owner: 'builder', members: [],
  my_access: ['read', 'operate', 'build', 'members:manage', 'delete'], warnings: [], ...extra,
});
