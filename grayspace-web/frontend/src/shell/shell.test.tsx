import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '../auth/AuthProvider';
import { RequireAuth } from '../auth/RequireAuth';
import { AppRoutes } from '../App';
import { ROUTER_FUTURE } from './router';
import { apiService, websocketBase } from '../services/api';
import { clearToken, setToken } from '../services/auth';
import { deployment, fakeServer, me, SilentWebSocket } from '../testing';
import { deploymentState, isLive } from './deployments';
import { DeploymentsProvider } from './DeploymentsProvider';
import { Header } from './Header';
import { SignInPage } from '../auth/SignInPage';

// The workspace draws the map (react-leaflet, ESM only); these tests are about what surrounds it
jest.mock('../pages/workspace/Workspace', () => ({
  Workspace: ({ deployment, section }: { deployment: { id: string }; section: string }) => <div>workspace {deployment.id} {section}</div>,
}));

beforeEach(() => {
  (global as unknown as { WebSocket: unknown }).WebSocket = SilentWebSocket;
  clearToken();
});

const LIVE = { core: { status: 'healthy', version: { name: 'grayspace/2026.11', csv_sha256: 'x', scope: 'full' }, scope: 'full', routers: 134 } };

describe('deployment state', () => {
  it('follows the base, the jobs and the draft', () => {
    expect(deploymentState(deployment('a'))).toBe('none');
    expect(deploymentState(deployment('a', { summary: { instances_live: 0, instances_failed: 0, draft: { saved: true, changes: true } } }))).toBe('draft');
    expect(deploymentState(deployment('a', { summary: { instances_live: 0, instances_failed: 0, active_job: { id: 'j', kind: 'rollout', state: 'running' } } }))).toBe('deploying');
    expect(deploymentState(deployment('a', { summary: { instances_live: 0, instances_failed: 0, last_core_job: { id: 'j', kind: 'deploy_base', state: 'failed' } } }))).toBe('failed');
    expect(deploymentState(deployment('a', LIVE))).toBe('live');
    expect(deploymentState(deployment('a', { core: { status: 'degraded' } }))).toBe('degraded');
    expect(deploymentState(deployment('a', { worker: { state: 'crashed', restarts: 3 } }))).toBe('down');
    expect(isLive(deployment('a', { core: { status: 'unknown' } }))).toBe(true);
  });
});

describe('the deployment API', () => {
  it('is under the open deployment', async () => {
    const calls = fakeServer(() => ({ body: [] }));
    apiService.setDeployment('dev');
    await apiService.getRouters();
    expect(calls[0].url).toBe('/deployments/dev/api/v1/topology/routers');
    expect(websocketBase()).toMatch(/\/deployments\/dev$/);
    apiService.setDeployment(null);
  });
});

describe('sign-in', () => {
  it('sends a signed-out user to sign in, and back after', async () => {
    fakeServer((url, init) => {
      if (url.endsWith('/api/v1/auth/providers')) return { body: { providers: ['local'] } };
      if (url.endsWith('/api/v1/auth/login')) {
        const { password } = JSON.parse(String(init?.body));
        return password === 'right password!' ? { body: { token: 'gss_t', session: {}, must_change_password: false } } : { status: 401, body: { detail: 'Wrong username or password' } };
      }
      if (url.endsWith('/api/v1/me')) return { body: me('builder') };
      return undefined;
    });
    render(
      <MemoryRouter future={ROUTER_FUTURE} initialEntries={['/secret']}>
        <AuthProvider>
          <Routes>
            <Route path="/signin" element={<SignInPage />} />
            <Route path="/secret" element={<RequireAuth><div>the secret page</div></RequireAuth>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    fireEvent.change(await screen.findByLabelText('Username'), { target: { value: 'builder' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong username or password');
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'right password!' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('the secret page')).toBeInTheDocument();
  });

  it('asks for a new password after a one-time one', async () => {
    setToken('gss_t');
    fakeServer((url) => (url.endsWith('/api/v1/me') ? { body: me('user', 'uma', { must_change_password: true }) } : undefined));
    render(<MemoryRouter future={ROUTER_FUTURE} initialEntries={['/']}><AuthProvider><AppRoutes /></AuthProvider></MemoryRouter>);
    expect(await screen.findByText('Choose a new password')).toBeInTheDocument();
  });
});

const renderAt = (path: string, deployments: ReturnType<typeof deployment>[], who = me('builder')) => {
  setToken('gss_t');
  fakeServer((url) => {
    if (url.endsWith('/api/v1/me')) return { body: who };
    if (url.endsWith('/api/v1/deployments')) return { body: deployments };
    return { body: [] };
  });
  return render(<MemoryRouter future={ROUTER_FUTURE} initialEntries={[path]}><AuthProvider><AppRoutes /></AuthProvider></MemoryRouter>);
};

describe('the header', () => {
  const header = (path: string, d: ReturnType<typeof deployment>) => {
    setToken('gss_t');
    fakeServer((url) => (url.endsWith('/api/v1/deployments') ? { body: [d] } : url.endsWith('/api/v1/me') ? { body: me('builder') } : undefined));
    return render(
      <MemoryRouter future={ROUTER_FUTURE} initialEntries={[path]}>
        <AuthProvider><DeploymentsProvider><Routes><Route path="*" element={<Header />} /></Routes></DeploymentsProvider></AuthProvider>
      </MemoryRouter>,
    );
  };

  it('shows only Build before the base is live', async () => {
    header('/d/ex1/build', deployment('ex1'));
    expect(await screen.findByRole('link', { name: 'Build' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Jobs' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Map' })).not.toBeInTheDocument();
  });

  it('shows the operate tabs and Configuration once it is', async () => {
    header('/d/ex1/map', deployment('ex1', LIVE));
    expect(await screen.findByRole('link', { name: 'Configuration' })).toBeInTheDocument();
    for (const name of ['Overview', 'Map', 'Enclaves', 'DNS', 'Certificates', 'Jobs']) expect(screen.getByRole('link', { name })).toBeInTheDocument();
  });
});

describe('the deployments home', () => {
  it('lists them, and offers New deployment to builders', async () => {
    renderAt('/', [deployment('ex1', LIVE), deployment('lab')]);
    expect(await screen.findByText('ex1')).toBeInTheDocument();
    expect(screen.getByText('lab')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '＋ New deployment' })).toBeInTheDocument();
  });

  it("doesn't offer New deployment to users", async () => {
    renderAt('/', [deployment('ex1', { ...LIVE, my_access: ['read', 'operate'] })], me('user', 'uma'));
    expect(await screen.findByText('ex1')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '＋ New deployment' })).not.toBeInTheDocument();
  });

  it('sends a deployment without a base to Build, not its map', async () => {
    renderAt('/d/lab/map', [deployment('lab')]);
    expect(await screen.findByText('workspace lab build')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Build' })).toHaveClass('active');
  });

  it('opens a live deployment on its overview', async () => {
    renderAt('/d/ex1', [deployment('ex1', LIVE)]);
    expect(await screen.findByText('workspace ex1 overview')).toBeInTheDocument();
  });

  it('keeps Jobs open before the base is live', async () => {
    renderAt('/d/lab/jobs/j1', [deployment('lab')]);
    expect(await screen.findByText('workspace lab jobs')).toBeInTheDocument();
  });

  it('says so when a deployment is not yours', async () => {
    renderAt('/d/other/map', [deployment('ex1')]);
    expect(await screen.findByText(/isn't yours to see/)).toBeInTheDocument();
  });
});
