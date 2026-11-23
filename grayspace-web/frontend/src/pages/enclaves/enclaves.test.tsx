import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ROUTER_FUTURE } from '../../shell/router';
import { apiService } from '../../services/api';
import { deployment, fakeServer } from '../../testing';
import { InstanceView, ManifestHost } from '../../types/slices';
import { EnclavesPage } from './EnclavesPage';

const ref = { slug: 'residential-customer', version: 'sha256:1' };
const host = (name: string, image: string, extra: Partial<ManifestHost> = {}): ManifestHost => ({
  name, hostname: `rc-${name}`, site: 'home', segment: 'home-lan', ip: '192.168.0.10', public_ip: null, mgmt_ip: '10.0.5.20',
  fqdns: [], roles: [], labels: {}, image, status: 'running', ...extra,
});
const view: InstanceView = {
  id: 'rc', slice: ref, enclave: ref, core: null, state: 'ready', dns: [], certificates: [],
  sites: [{
    name: 'home', router: 'rog-tor', isp: 'Rogers', location: 'Toronto', wan_ip: '64.231.0.2', routed: false, public_prefix: null,
    wans: [{ router: 'rog-tor', isp: 'Rogers', asn: 812, location: 'Toronto', customer_net: '64.231.0.0/24', bridge: 'v100', ip: '64.231.0.2', gateway: '64.231.0.1' }],
    edge: 'rc-home-edge', segments: [{ name: 'home-lan', cidr: '192.168.0.0/24', gateway: '192.168.0.1' }],
  }],
  hosts: [host('ws-01', 'grayspace-desktop'), host('ws-02', 'grayspace-desktop', { status: 'stopped' }), host('srv', 'grayspace-ubuntu-lxc'),
    host('web', 'grayspace-ubuntu-lxc', { site: null, segment: null, ip: '203.0.113.5' })],
  created: '2026-11-16T00:00:00+00:00', updated: '2026-11-16T00:00:00+00:00', error: null, stages_done: [], jobs: [],
};

beforeEach(() => apiService.setDeployment('ex1'));
afterEach(() => apiService.setDeployment(null));

const renderPage = (access = ['read', 'operate', 'build']) => {
  fakeServer((url) => {
    if (url.includes('/api/v1/instances/rc')) return { body: view };
    if (url.includes('/api/v1/instances')) return { body: [{ id: 'rc', slice: ref, enclave: ref, state: 'ready', created: view.created, updated: view.updated, error: null, hosts: 4, sites: 1, last_job: null }] };
    if (url.includes('/api/v1/jobs')) return { body: [] };
    if (url.endsWith('/api/v1/images')) return {
      body: [
        { name: 'grayspace-desktop', type: 'lxc', os_family: 'ubuntu', source: 'x', cores: 2, memory: 2048, disk: 16, features: ['desktop'], description: '', origin: 'shipped' },
        { name: 'grayspace-ubuntu-lxc', type: 'lxc', os_family: 'ubuntu', source: 'x', cores: 1, memory: 1024, disk: 8, features: [], description: '', origin: 'shipped' },
      ],
    };
    return undefined;
  });
  const onOpenDesktop = jest.fn();
  render(
    <MemoryRouter future={ROUTER_FUTURE}>
      <EnclavesPage deployment={deployment('ex1', { my_access: access })} selectedId="rc" onSelect={() => undefined}
        onShowOnMap={() => undefined} onOpenConsole={() => undefined} onOpenDesktop={onOpenDesktop} />
    </MemoryRouter>,
  );
  return onOpenDesktop;
};

describe('enclave hosts', () => {
  it('open a desktop when their image has one and they run', async () => {
    const onOpenDesktop = renderPage();
    const desktops = await screen.findAllByRole('button', { name: 'Desktop' });
    expect(desktops).toHaveLength(1);  // not the stopped workstation, nor the host without a desktop
    fireEvent.click(desktops[0]);
    expect(onOpenDesktop).toHaveBeenCalledWith('rc-ws-01');
  });

  it('offer no desktop to readers', async () => {
    renderPage(['read']);
    await screen.findByText('rc-ws-01');
    expect(screen.queryByRole('button', { name: 'Desktop' })).toBeNull();
  });
});

describe('an instance', () => {
  it('opens on its hosts under their sites', async () => {
    renderPage();
    await screen.findByText('rc-ws-01');
    expect(screen.getByRole('heading', { name: /^home/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /^Internet/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Destroy' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Jobs' })).toBeNull();
  });

  it('keeps jobs and Destroy under Manage', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: '⚙ Manage' }));
    expect(screen.getByRole('heading', { name: 'Jobs' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Destroy' })).toBeInTheDocument();
    expect(screen.queryByText('rc-ws-01')).toBeNull();
  });
});
