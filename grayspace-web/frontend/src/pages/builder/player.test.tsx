import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { apiService } from '../../services/api';
import { DeploymentsProvider } from '../../shell/DeploymentsProvider';
import { ROUTER_FUTURE } from '../../shell/router';
import { deployment, fakeServer, SilentWebSocket } from '../../testing';
import { BuildWizard } from './BuildWizard';

const GUESTS = {
  base: [
    { hostname: 'lum-chi', group: 'routers', isp: 'Lumen' }, { hostname: 'lum-nyc', group: 'routers', isp: 'Lumen' },
    { hostname: 'com-nyc', group: 'routers', isp: 'Comcast' }, { hostname: 'root-a', group: 'core_services', role: 'root-dns' },
    { hostname: 'prometheus', group: 'management' },
  ],
  enclaves: [{ prefix: 'mb', slug: 'meridian-bank', sites: [{ name: 'hq', edge: 'mb-hq-edge', hint: true }],
    hosts: [{ name: 'web01', count: 1, image: 'ubuntu', hint: false }, { name: 'ws', count: 40, image: 'ubuntu', hint: true }] }],
};
const DRAFT = { base: { version: 'grayspace/2026.11', scope: 'full', configure: true }, enclaves: [{ slug: 'meridian-bank', prefix: 'mb', version: null }] };

beforeEach(() => {
  (global as unknown as { WebSocket: unknown }).WebSocket = SilentWebSocket;
  apiService.setDeployment('ex1');
});
afterEach(() => apiService.setDeployment(null));

const serve = (draft: unknown = DRAFT) => fakeServer((url, init) => {
  if (url.endsWith('/api/v1/draft') && init?.method === 'PUT') return { body: { ...JSON.parse(String(init.body)), updated: new Date().toISOString() } };
  if (url.endsWith('/api/v1/draft')) return { body: draft };
  if (url.endsWith('/api/v1/base/versions')) return { body: [{ name: 'grayspace/2026.11', csv_sha256: 'x', deployed: true, problems: [], summary: null }] };
  if (url.endsWith('/api/v1/enclaves')) return { body: [] };
  if (url.includes('/api/v1/capabilities')) return { body: null };
  if (url.endsWith('/api/v1/player/templates')) return { body: [{ id: 't-vms', name: 'VMs', embeddable: true }, { id: 't-map', name: 'Map' }] };
  if (url.endsWith('/api/v1/player/roles')) return { body: [{ id: 'r1', name: 'View Member' }, { id: 'r2', name: 'Observer' }] };
  if (url.includes('/api/v1/player/guests')) return { body: GUESTS };
  if (url.endsWith('/api/v1/deployments')) return { body: [] };
  return undefined;
});

const wizard = (step: string, player = true) => render(
  <MemoryRouter future={ROUTER_FUTURE}><DeploymentsProvider>
    <BuildWizard deployment={deployment('ex1', { options: { player } })} step={step} />
  </DeploymentsProvider></MemoryRouter>,
);

describe('the Player step', () => {
  it('is a step only with the Player option', async () => {
    serve();
    wizard('certificates', false);
    expect(await screen.findByRole('button', { name: 'Next: Review & deploy' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Player' })).not.toBeInTheDocument();
  });

  it('pre-assigns what the spec marks, assigns groups, and saves the view in the draft', async () => {
    const calls = serve();
    wizard('player');
    expect(await screen.findByText('meridian-bank · mb')).toBeInTheDocument();
    // The spec's hints start in the default team; the rest in none
    expect(screen.getByLabelText('ws ×40 in Blue')).toBeChecked();
    expect(screen.getByLabelText('hq (site) in Blue')).toBeChecked();
    expect(screen.getByLabelText('web01 in Blue')).not.toBeChecked();
    expect(screen.getAllByText('from the spec')).toHaveLength(2);

    fireEvent.change(screen.getByLabelText('Add an application'), { target: { value: 't-vms' } });
    expect(screen.getByLabelText('Blue gets VMs')).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: '＋ Team' }));
    expect(screen.getAllByLabelText('Team name')[1]).toHaveValue('Red');
    // Every Lumen router to Red; one hinted host out of Blue
    fireEvent.click(screen.getByLabelText('Routers · Lumen: all to Red'));
    fireEvent.click(screen.getByLabelText('ws ×40 in Blue'));
    fireEvent.click(screen.getByRole('button', { name: 'Next: Review & deploy' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/api/v1/draft') && c.init?.method === 'PUT')).toBe(true));
    const body = JSON.parse(String(calls.find((c) => c.init?.method === 'PUT')!.init?.body));
    expect(body.player.applications).toEqual([{ name: 'VMs', template_id: 't-vms', embeddable: true }]);
    expect(body.player.teams).toEqual([{ name: 'Blue', role: 'View Member', applications: ['VMs'] }, { name: 'Red', role: 'View Member', applications: ['VMs'] }]);
    expect(body.player.guests).toEqual([
      { name: 'lum-chi', teams: ['Red'] }, { name: 'lum-nyc', teams: ['Red'] },
      { name: 'ws', instance: 'mb', teams: [] },
    ]);
  });

  it('gives the whole base or an enclave to a team as a rule, with exceptions', async () => {
    const calls = serve();
    wizard('player');
    fireEvent.click(await screen.findByLabelText('Base: all to Blue'));
    fireEvent.click(screen.getByLabelText('meridian-bank · mb: all to Blue'));
    expect(screen.getByLabelText('web01 in Blue')).toBeChecked();
    // One base router out
    fireEvent.click(within(screen.getByText('Routers · Lumen').closest('td')!).getByRole('button'));
    fireEvent.click(screen.getByLabelText('lum-chi in Blue'));
    expect(screen.getByLabelText('Base: all to Blue')).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Next: Review & deploy' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/api/v1/draft') && c.init?.method === 'PUT')).toBe(true));
    const body = JSON.parse(String(calls.find((c) => c.init?.method === 'PUT')!.init?.body));
    expect(body.player.assign).toEqual([{ scope: 'base', teams: ['Blue'] }, { scope: 'enclave:mb', teams: ['Blue'] }]);
    // The hinted hosts follow the rule now, so they need no entry; the one router left out does
    expect(body.player.guests).toEqual([{ name: 'lum-chi', teams: [] }]);
  });

  it('drops entries for base guests the base no longer has', async () => {
    const calls = serve({ ...DRAFT, player: { applications: [], teams: [{ name: 'Blue', applications: [] }], guests: [{ name: 'grafana', teams: ['Blue'] }] } });
    wizard('player');
    fireEvent.click(await screen.findByLabelText('Base: all to Blue'));
    fireEvent.click(screen.getByRole('button', { name: 'Next: Review & deploy' }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === 'PUT')).toBe(true));
    expect(JSON.parse(String(calls.find((c) => c.init?.method === 'PUT')!.init?.body)).player.guests).toEqual([]);
  });

  it('filters, and renames a team everywhere', async () => {
    serve({ ...DRAFT, player: { applications: [], teams: [{ name: 'Blue', applications: [] }], default_team: 'Blue', guests: [{ name: 'com-nyc', teams: ['Blue'] }] } });
    wizard('player');
    fireEvent.change(await screen.findByLabelText('Filter resources'), { target: { value: 'comcast' } });
    expect(screen.getByLabelText('com-nyc in Blue')).toBeChecked();
    expect(screen.queryByLabelText('lum-chi in Blue')).not.toBeInTheDocument();
    await act(async () => { fireEvent.change(screen.getByLabelText('Team name'), { target: { value: 'Gold' } }); });
    expect(screen.getByLabelText('com-nyc in Gold')).toBeChecked();
    expect(within(screen.getByRole('table')).getByText('Gold')).toBeInTheDocument();
  });

  it('can be left out of the draft', async () => {
    const calls = serve({ ...DRAFT, player: { applications: [], teams: [{ name: 'Blue', applications: [] }], guests: [] } });
    window.confirm = jest.fn(() => true);
    wizard('player');
    fireEvent.click(await screen.findByRole('button', { name: 'Remove the view' }));
    expect(screen.getByRole('button', { name: 'Add a Player view' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next: Review & deploy' }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === 'PUT')).toBe(true));
    expect(JSON.parse(String(calls.find((c) => c.init?.method === 'PUT')!.init?.body)).player).toBeNull();
  });
});
