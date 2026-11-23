import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '../../auth/AuthProvider';
import { setToken } from '../../services/auth';
import { DeploymentsProvider } from '../../shell/DeploymentsProvider';
import { Header } from '../../shell/Header';
import { ROUTER_FUTURE } from '../../shell/router';
import { deployment, fakeServer, me, PLATFORM, SilentWebSocket } from '../../testing';
import { DeploymentSettings } from '../settings/DeploymentSettings';
import { AdminPage } from './AdminPage';

// Made per run, so it reads as what it is: no real password
const ONE_TIME = Math.random().toString(36).slice(2, 10);

const USERS = [
  { username: 'admin', name: 'admin', email: '', role: 'admin', source: 'local', disabled: false, must_change_password: false, last_seen: null },
  { username: 'uma', name: 'Uma', email: 'uma@x', role: 'user', source: 'local', disabled: false, must_change_password: false, last_seen: null },
];

const SAM = { ...USERS[1], username: 'sam' };
const CREATED = { user: SAM, one_time_password: ONE_TIME };

beforeEach(() => {
  (global as unknown as { WebSocket: unknown }).WebSocket = SilentWebSocket;
  setToken('gss_t');
});

const app = (path: string, who: ReturnType<typeof me>, deployments = [deployment('ex1', { owner: 'blake' })], extra: (u: string, i?: RequestInit) => { status?: number; body?: unknown } | undefined = () => undefined) => {
  const calls = fakeServer((url, init) => {
    const answer = extra(url, init);
    if (answer) return answer;
    if (url.endsWith('/api/v1/me')) return { body: who };
    if (url.endsWith('/api/v1/deployments')) return { body: deployments };
    if (url.endsWith('/api/v1/users') && init?.method === 'POST') return { status: 201, body: CREATED };
    if (url.endsWith('/api/v1/users')) return { body: USERS };
    if (url.endsWith('/api/v1/directory')) return { body: [{ username: 'blake', name: 'Blake', role: 'builder' }, { username: 'uma', name: 'Uma', role: 'user' }, { username: 'cara', name: 'Cara', role: 'builder' }] };
    if (url.endsWith('/api/v1/platform')) return { body: PLATFORM };
    if (url.endsWith('/api/v1/platform/proxmox') && init?.method === 'POST') return { status: 201, body: PLATFORM };
    if (url.endsWith('/api/v1/platform/proxmox/site/test')) return { body: { ok: false, checks: [{ name: 'api', status: 'ok', message: 'Proxmox VE 8.2 accepts the credentials' }, { name: 'node pve2', status: 'error', message: 'No node pve2' }] } };
    if (url.includes('/api/v1/nodes')) return { body: [{ host: 'pve1.example', node: 'pve1', deployments: ['ex1'], capacity: { cpus: 64, cpu_usage: 0.5, memory_total: 100, memory_free: 10 }, locked: true, lock_holder: 'ex1' }] };
    if (url.includes('/api/v1/audit')) return { body: [{ at: new Date().toISOString(), user: 'blake', method: 'POST', path: '/deployments/ex1/api/v1/deploy', deployment: 'ex1', status: 202 }] };
    return undefined;
  });
  render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={[path]}>
      <AuthProvider><DeploymentsProvider>
        <Header />
        <Routes>
          <Route path="/admin/:tab" element={<AdminPage />} />
          <Route path="/d/:dep/settings" element={<DeploymentSettings deployment={deployments[0]} />} />
          <Route path="*" element={<div>elsewhere</div>} />
        </Routes>
      </DeploymentsProvider></AuthProvider>
    </MemoryRouter>,
  );
  return calls;
};

describe('the admin area', () => {
  it('is in the user menu for admins only', async () => {
    app('/', me('admin'));
    fireEvent.click(await screen.findByTitle('admin'));
    for (const name of ['Platform', 'Users & roles', 'API keys', 'Images & roles', 'Audit log']) expect(screen.getByRole('link', { name })).toBeInTheDocument();
  });

  it("isn't for builders", async () => {
    app('/admin/users', me('builder', 'blake'));
    fireEvent.click(await screen.findByTitle('blake'));
    expect(screen.queryByRole('link', { name: 'Users & roles' })).not.toBeInTheDocument();
    expect(screen.getByText('The admin area is for admins.')).toBeInTheDocument();
  });

  it('makes a local user and shows the one-time password once', async () => {
    const calls = app('/admin/users', me('admin'));
    expect(await screen.findByText('Uma')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '＋ Local user' }));
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'Sam' } });
    fireEvent.click(screen.getByRole('checkbox'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create user' })); });
    expect(await screen.findByTestId('one-time-password')).toHaveTextContent(ONE_TIME);
    const post = calls.find((c) => c.url.endsWith('/api/v1/users') && c.init?.method === 'POST')!;
    expect(JSON.parse(String(post.init?.body))).toEqual({ username: 'sam', name: '', email: '', role: 'user', deployments: ['ex1'] });
  });

  it('finds users by name, username or email', async () => {
    app('/admin/users', me('admin'));
    expect(await screen.findByText('Uma')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search users'), { target: { value: 'UMA@' } });
    expect(screen.queryByLabelText('admin role')).not.toBeInTheDocument();
    expect(screen.getByLabelText('uma role')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search users'), { target: { value: 'nobody' } });
    expect(screen.getByText('No users match.')).toBeInTheDocument();
  });

  it("can't demote or disable yourself", async () => {
    app('/admin/users', me('admin'));
    expect(await screen.findByLabelText('admin role')).toBeDisabled();
    expect(screen.getByLabelText('uma role')).toBeEnabled();
  });

  it("colors a node by how full it is, opens its capacity and lock, and shows the audit log", async () => {
    app('/admin/platform', me('admin'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'pve1 ★' })).toHaveClass('warn'));
    expect(screen.getByRole('button', { name: 'pve1 ★' }).title).toContain('Memory 90% used');
    fireEvent.click(screen.getByRole('button', { name: 'pve1 ★' }));
    expect(screen.getByText(/held by ex1's job/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByRole('link', { name: 'Audit log' }));
    expect(await screen.findByText('POST /deployments/ex1/api/v1/deploy')).toBeInTheDocument();
  });
});

describe('the platform', () => {
  it('shows the connections, tests one, and adds one with its nodes', async () => {
    const calls = app('/admin/platform', me('admin'));
    expect(await screen.findByText('pve1.example:8006')).toBeInTheDocument();
    expect(screen.getByText('https://c/player/api')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Test' })[0]);
    expect(await screen.findByText('No node pve2')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
    // In use: not deletable
    expect(screen.getAllByRole('button', { name: 'Delete' })[0]).toBeDisabled();

    fireEvent.click(screen.getAllByRole('button', { name: '＋ Connection' })[0]);
    fireEvent.change(screen.getByLabelText('ID'), { target: { value: 'Lab' } });
    fireEvent.change(screen.getByLabelText('API host'), { target: { value: 'pve-lab.example' } });
    fireEvent.change(screen.getByLabelText(/API token/), { target: { value: 'lab@pve!t=1' } });
    fireEvent.change(screen.getByLabelText('Node name'), { target: { value: 'lab1' } });
    fireEvent.change(screen.getByLabelText('VM bridge'), { target: { value: 'vmbr9' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Add the connection' })); });
    const post = calls.find((c) => c.url.endsWith('/api/v1/platform/proxmox') && c.init?.method === 'POST')!;
    expect(JSON.parse(String(post.init?.body))).toEqual({ id: 'lab', host: 'pve-lab.example', port: 8006, auth: 'token', ssh_username: 'root',
      proxy_mode: 'site', api_token: 'lab@pve!t=1', nodes: [{ name: 'lab1', vm_bridge: 'vmbr9' }] });
    expect(await screen.findByText('Added lab')).toBeInTheDocument();
  });

  it("sets a cluster's parallelism, and clears it back to the server's default", async () => {
    const saved = { ...PLATFORM, proxmox: [{ ...PLATFORM.proxmox[0], plan_parallelism: 40 }] };
    const patched = (u: string, i?: RequestInit) => (u.endsWith('/api/v1/platform/proxmox/site') && i?.method === 'PATCH' ? { body: { restart_pending: [] } }
      : u.endsWith('/api/v1/platform') ? { body: saved } : undefined);
    const calls = app('/admin/platform', me('admin'), undefined, patched);
    expect(await screen.findByText(/40 parallel reads/)).toBeInTheDocument();
    fireEvent.click((await screen.findAllByRole('button', { name: 'Edit' }))[0]);
    expect(screen.getByLabelText('Guests created at once')).toHaveValue(null);
    expect(screen.getByLabelText('Parallel reads in a plan')).toHaveValue(40);

    fireEvent.change(screen.getByLabelText('Guests created at once'), { target: { value: '0' } });
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.getByText('A whole number from 1 to 100')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Guests created at once'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('Parallel reads in a plan'), { target: { value: '' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
    const patch = calls.find((c) => c.url.endsWith('/api/v1/platform/proxmox/site') && c.init?.method === 'PATCH')!;
    const body = JSON.parse(String(patch.init?.body));
    // Emptied: 0, which clears the saved value
    expect([body.apply_parallelism, body.plan_parallelism]).toEqual([5, 0]);
  });

  it("chooses a cluster's proxy for its guests", async () => {
    const patched = (u: string, i?: RequestInit) => (u.endsWith('/api/v1/platform/proxmox/site') && i?.method === 'PATCH' ? { body: { restart_pending: [] } }
      : undefined);
    const calls = app('/admin/platform', me('admin'), undefined, patched);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Edit' }))[0]);
    expect(screen.getByRole('button', { name: "The site's" })).toHaveClass('on');
    expect(screen.queryByLabelText('Proxy')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'This one' }));
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();  // needs a URL
    fireEvent.change(screen.getByLabelText('Proxy'), { target: { value: 'proxy.example:3128' } });
    expect(screen.getByText('An http:// or https:// URL')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Proxy'), { target: { value: 'http://proxy.example:3128' } });
    fireEvent.change(screen.getByLabelText('Reached without it'), { target: { value: '10.0.0.0/8' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
    const patch = calls.find((c) => c.url.endsWith('/api/v1/platform/proxmox/site') && c.init?.method === 'PATCH')!;
    const body = JSON.parse(String(patch.init?.body));
    expect([body.proxy_mode, body.proxy, body.no_proxy]).toEqual(['custom', 'http://proxy.example:3128', '10.0.0.0/8']);
  });

  it('never shows a secret, only whether it is set', async () => {
    app('/admin/platform', me('admin'));
    fireEvent.click((await screen.findAllByRole('button', { name: 'Edit' }))[0]);
    expect(screen.getByLabelText(/API token/)).toHaveValue('');
    expect(screen.getByLabelText(/API token/)).toHaveAttribute('placeholder', 'set; type to replace');
  });
});

describe("a deployment's settings", () => {
  it('adds members, with build only for builders', async () => {
    const d = deployment('ex1', { owner: 'blake', members: [{ user: 'uma', access: 'use' }] });
    const calls = app('/d/ex1/settings', me('builder', 'blake'), [d], (url, init) =>
      (url.endsWith('/members') ? { body: { ...d, members: JSON.parse(String(init?.body)).members } } : undefined));
    const access = await screen.findByLabelText('uma access');
    await waitFor(() => expect((access.querySelector('option[value=build]') as HTMLOptionElement).disabled).toBe(true));
    fireEvent.change(screen.getByLabelText('Add a member'), { target: { value: 'cara' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Add with use access' })); });
    const put = calls.find((c) => c.url.endsWith('/api/v1/deployments/ex1/members'))!;
    expect(JSON.parse(String(put.init?.body))).toEqual({ members: [{ user: 'uma', access: 'use' }, { user: 'cara', access: 'use' }] });
    expect(screen.getByRole('link', { name: 'Settings' })).toBeInTheDocument();
  });

  it('links to the Player view once there is one', async () => {
    const d = deployment('ex1', { options: { player: true, player_connection: 'cru' } });
    app('/d/ex1/settings', me('builder'), [d], (url) =>
      (url.endsWith('/api/v1/player') ? { body: { view: 'ex1-grayspace', view_id: 'v-1', url: 'https://c/player/view/v-1' } } : undefined));
    expect(await screen.findByRole('link', { name: 'Open in Player ↗' })).toHaveAttribute('href', 'https://c/player/view/v-1');
  });

  it("locks where it runs, and removing it, while it's deployed", async () => {
    const d = deployment('ex1', { core: { status: 'healthy' }, placement: { connection: 'site', node: 'pve1' } });
    app('/d/ex1/settings', me('builder'), [d]);
    expect(await screen.findByLabelText('Node')).toBeDisabled();
    expect(screen.getByLabelText('Proxmox connection')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Remove the deployment' })).toBeDisabled();
  });
});
