import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { apiService } from '../../services/api';
import { ROUTER_FUTURE } from '../../shell/router';
import { DeploymentsProvider } from '../../shell/DeploymentsProvider';
import { deployment, fakeServer, SilentWebSocket } from '../../testing';
import { EnclaveSpec } from '../../types/build';
import { BuildWizard } from './BuildWizard';
import { DnsStep } from './DnsStep';
import { ReadinessChecks } from './ReadinessChecks';
import { Builder } from './useBuilder';
import { EnclaveEditor } from './EnclaveEditor';

// The preview's map (react-leaflet doesn't load in jest): what it would draw
jest.mock('../../components/MapView', () => ({
  MapView: ({ layer }: { layer: { features: unknown[] } }) => <div data-testid="preview-map">{layer.features.length} features</div>,
}));

const CAPS = {
  core: { name: 'grayspace/2026.11', csv_sha256: 'x', scope: 'full' },
  images: [{ name: 'grayspace-ubuntu-lxc', type: 'lxc', os_family: 'ubuntu', description: '' }],
  roles: [
    { name: 'web', description: 'nginx', os_families: ['ubuntu'] },
    { name: 'ad-dc', description: 'Samba AD', os_families: ['ubuntu'], variables: {
      realm: { description: 'The realm', required: true },
      admin_password: { description: 'The Administrator password', required: true },
    } },
    { name: 'file', description: 'Samba share', os_families: ['ubuntu'], variables: {
      share_name: { description: 'Name of the share', default: 'shared' },
      guest_ok: { description: 'Allow guests', default: true },
    } },
  ],
  dns: { tlds: ['com'], hosting: [{ hostname: 'dns-cloudflare', isp: 'Lumen' }], resolvers: [] },
  cas: [{ hostname: 'ca-graytrust', name: 'GrayTrust', isp: 'Lumen' }],
  isps: [{ name: 'Lumen', asn: 3356, locations: ['Chicago', 'New York'] }, { name: 'Comcast', asn: 7922, locations: ['New York'] }],
  capacity: { cpus: 64, cpu_usage: 0.1, memory_total: 100, memory_free: 40, storage_total: 100, storage_free: 90 },
  jobs: true,
};
const BANK: EnclaveSpec = {
  apiVersion: 'grayspace/v1', kind: 'Slice', slug: 'meridian-bank', prefix: 'mb', description: 'A bank',
  sites: [{ name: 'hq', attach: { isp: 'Lumen', location: 'Chicago' }, segments: [{ name: 'hq-lan' }] }],
  hosts: [{ name: 'web01', segment: 'hq-lan', image: 'grayspace-ubuntu-lxc', roles: ['web'] }],
  role_vars: { web: { port: 8080 } },
};

beforeEach(() => {
  (global as unknown as { WebSocket: unknown }).WebSocket = SilentWebSocket;
  apiService.setDeployment('ex1');
});
afterEach(() => apiService.setDeployment(null));

describe('the readiness checks', () => {
  const checks = [
    { name: 'jobs', status: 'ok', message: 'This GraySpace can run core jobs' },
    { name: 'version', status: 'ok', message: 'grayspace/2026.11 at full scope' },
    { name: 'proxmox', status: 'ok', message: "Node 'pve' answers" },
    { name: 'bridges', status: 'error', message: "Bridge 'vmbr9' doesn't exist" },
  ] as const;

  it('are one line per group, opened to see each check; the server only when something is wrong', () => {
    render(<ReadinessChecks preflight={{ ready: false, version: 'grayspace/2026.11', scope: 'full', checks: [...checks] }} />);
    expect(screen.getByText('Core version')).toBeInTheDocument();
    expect(screen.getByText("Bridge 'vmbr9' doesn't exist")).toBeInTheDocument();
    expect(screen.queryByText('GraySpace server')).not.toBeInTheDocument();
    expect(screen.queryByText("Node 'pve' answers")).not.toBeInTheDocument();
    expect(screen.getByText('Contact your administrator.')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Proxmox'));
    expect(screen.getByText("Node 'pve' answers")).toBeInTheDocument();
  });
});

describe('the DNS step', () => {
  const spec: EnclaveSpec = { ...BANK, dns: {
    domains: [{ domain: 'bank.com', hosting_server: 'dns-cloudflare' }, { domain: 'corp.net', hosting_server: 'dns-cloudflare' }],
    records: [{ name: 'www.bank.com', host: 'web01' }, { name: 'mail.corp.net', type: 'MX', data: '10 mx.corp.net' }, { name: 'stray.org', type: 'TXT', data: 'x' }],
  } };
  const Harness: React.FC<{ onChange: (s: EnclaveSpec) => void }> = ({ onChange }) => {
    const [current, setCurrent] = React.useState(spec);
    const b = { capabilities: CAPS, enclaves: [{ slug: spec.slug, prefix: 'mb', version: null }], specs: { [spec.slug]: current },
      readOnly: () => false, updateSpec: (s: EnclaveSpec) => { setCurrent(s); onChange(s); } } as unknown as Builder;
    return <DnsStep b={b} readOnly={false} />;
  };

  it('lists each domain with its records, and renaming a domain renames its records', () => {
    const changes: EnclaveSpec[] = [];
    render(<Harness onChange={(s) => changes.push(s)} />);
    const names = () => screen.getAllByLabelText('Record name').map((e) => (e as HTMLInputElement).value);
    expect(names()).toEqual(['www.bank.com', 'mail.corp.net', 'stray.org']);
    expect(screen.getByText("Records outside the enclave's domains")).toBeInTheDocument();
    fireEvent.change(screen.getAllByLabelText('Domain')[0], { target: { value: 'meridian.com' } });
    expect(changes[changes.length - 1].dns!.records!.map((r) => r.name)).toEqual(['www.meridian.com', 'mail.corp.net', 'stray.org']);
  });
});

describe('the enclave editor', () => {
  const Harness: React.FC<{ onChange: (s: EnclaveSpec) => void }> = ({ onChange }) => {
    const [spec, setSpec] = React.useState(BANK);
    return <EnclaveEditor spec={spec} caps={CAPS as never} readOnly={false} onChange={(s) => { setSpec(s); onChange(s); }} />;
  };

  it('edits one spec in the form and as YAML, keeping what the form does not show', () => {
    const changes: EnclaveSpec[] = [];
    render(<Harness onChange={(s) => changes.push(s)} />);
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'A bank!' } });
    expect(changes[changes.length - 1].description).toBe('A bank!');
    fireEvent.click(screen.getByRole('button', { name: 'YAML' }));
    const yaml = screen.getByLabelText('YAML') as HTMLTextAreaElement;
    expect(yaml.value).toContain('prefix: mb');
    expect(yaml.value).toContain('port: 8080');
    fireEvent.change(yaml, { target: { value: yaml.value.replace('description: A bank!', 'description: A big bank') } });
    expect(changes[changes.length - 1].description).toBe('A big bank');
    expect(changes[changes.length - 1].role_vars).toEqual({ web: { port: 8080 } });
    fireEvent.click(screen.getByRole('button', { name: 'Form' }));
    expect((screen.getByLabelText('Description') as HTMLInputElement).value).toBe('A big bank');
  });

  it('adds and removes a site\'s segments as chips', () => {
    const changes: EnclaveSpec[] = [];
    render(<Harness onChange={(s) => changes.push(s)} />);
    fireEvent.click(screen.getAllByRole('button', { name: '＋ Segment' })[0]);
    const input = screen.getByLabelText('New segment');
    fireEvent.change(input, { target: { value: 'dmz=10.9.0.0/24' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    const segments = () => changes[changes.length - 1].sites![0].segments;
    expect(segments()[segments().length - 1]).toEqual({ name: 'dmz', cidr: '10.9.0.0/24' });
    fireEvent.click(screen.getByRole('button', { name: 'Remove segment dmz' }));
    expect(segments().some((g) => g.name === 'dmz')).toBe(false);
  });

  it("refuses YAML that doesn't parse or renames the enclave", () => {
    const changes: EnclaveSpec[] = [];
    render(<Harness onChange={(s) => changes.push(s)} />);
    fireEvent.click(screen.getByRole('button', { name: 'YAML' }));
    const yaml = screen.getByLabelText('YAML') as HTMLTextAreaElement;
    fireEvent.change(yaml, { target: { value: yaml.value.replace('slug: meridian-bank', 'slug: other') } });
    expect(screen.getByText(/save a copy under another name/)).toBeInTheDocument();
    fireEvent.change(yaml, { target: { value: 'sites: [' } });
    expect(changes).toHaveLength(0);
  });

  it('asks for the variables of the roles the hosts use, once per role, and opens when one is required', () => {
    const changes: EnclaveSpec[] = [];
    render(<Harness onChange={(s) => changes.push(s)} />);
    // web declares no variables: nothing to show
    expect(screen.queryByRole('button', { name: /Role variables/ })).toBeNull();
    const last = () => changes[changes.length - 1];
    fireEvent.click(screen.getByRole('button', { name: '＋ Role' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'ad-dc' }));
    expect(screen.getByRole('button', { name: /Role variables/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('2 required values not set')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('ad-dc realm'), { target: { value: 'corp.example.com' } });
    expect(last().role_vars).toEqual({ web: { port: 8080 }, 'ad-dc': { realm: 'corp.example.com' } });
    const password = screen.getByLabelText('ad-dc admin_password');
    expect(password).toHaveAttribute('type', 'password');
    fireEvent.click(screen.getByRole('button', { name: 'Show ad-dc admin_password' }));
    expect(password).toHaveAttribute('type', 'text');
    fireEvent.change(password, { target: { value: 'pw' } });
    expect(screen.getByText('1 role, all set')).toBeInTheDocument();
    // An emptied value is removed, not saved as ""
    fireEvent.change(screen.getByLabelText('ad-dc realm'), { target: { value: '' } });
    expect(last().role_vars!['ad-dc']).toEqual({ admin_password: 'pw' });
  });

  it('shows defaults, and keeps the values of a role no host uses until cleared', () => {
    const changes: EnclaveSpec[] = [];
    render(<Harness onChange={(s) => changes.push(s)} />);
    const last = () => changes[changes.length - 1];
    fireEvent.click(screen.getByRole('button', { name: 'Remove web' }));
    fireEvent.click(screen.getByRole('button', { name: '＋ Role' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'file' }));
    // Nothing required: collapsed
    const toggle = screen.getByRole('button', { name: /Role variables/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(screen.getByLabelText('file share_name')).toHaveAttribute('placeholder', 'shared');
    fireEvent.change(screen.getByLabelText('file guest_ok'), { target: { value: 'false' } });
    expect(last().role_vars!.file).toEqual({ guest_ok: false });
    fireEvent.change(screen.getByLabelText('file guest_ok'), { target: { value: '' } });
    expect(last().role_vars!.file).toBeUndefined();
    // web's port stays, shown as unused, until cleared
    expect(screen.getByText(/unused: no host has this role \(port\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear the variables of web' }));
    expect(last().role_vars).toBeUndefined();
  });

  it('offers the base ISPs and their locations', () => {
    render(<Harness onChange={() => undefined} />);
    fireEvent.change(screen.getByLabelText('ISP'), { target: { value: 'Comcast' } });
    expect((screen.getByLabelText('Location') as HTMLSelectElement).value).toBe('');
    expect(Array.from((screen.getByLabelText('Location') as HTMLSelectElement).options).map((o) => o.value)).toEqual(['', 'New York']);
  });
});

const serve = (extra: (url: string, init?: RequestInit) => { status?: number; body?: unknown } | undefined = () => undefined) =>
  fakeServer((url, init) => {
    const answer = extra(url, init);
    if (answer) return answer;
    if (url.endsWith('/api/v1/draft') && init?.method === 'PUT') return { body: { ...JSON.parse(String(init.body)), updated: new Date().toISOString(), updated_by: 'blake' } };
    if (url.endsWith('/api/v1/draft')) return { body: { core: null, enclaves: [], adopted: true } };
    if (url.endsWith('/api/v1/core/versions')) return { body: [{ name: 'grayspace/2026.11', csv_sha256: 'x', deployed: false, problems: [], summary: { isps: 15, locations: 30, routers: 134, links: 200, core_services: 22, clients: 0 } }] };
    if (url.endsWith('/api/v1/enclaves')) return { body: [{ slug: 'base', kind: 'base', description: '', versions: [], instances: [] }, { slug: 'meridian-bank', kind: 'slice', description: 'A bank', versions: [], instances: [] }] };
    if (url.endsWith('/api/v1/enclaves/meridian-bank')) return { body: { slug: 'meridian-bank', kind: 'slice', description: '', versions: [], instances: [], spec: BANK } };
    if (url.includes('/validate')) return { body: { valid: true, version: 'sha256:1', findings: [] } };
    if (url.includes('/api/v1/capabilities')) return { body: CAPS };
    if (url.includes('/api/v1/core/preflight')) return { body: { ready: true, version: 'grayspace/2026.11', scope: 'full', checks: [{ name: 'version', status: 'ok', message: 'fine' }] } };
    if (url.endsWith('/api/v1/deploy/plan')) return { body: { core: { action: 'deploy', deployed: null, version: 'grayspace/2026.11', scope: 'full', configure: true }, instances: [{ id: 'mb', slug: 'meridian-bank', version: 'sha256:1', action: 'create', state: null }], unmanaged: [], problems: [], stages: ['base', 'create:mb'], changes: true, capacity: CAPS.capacity } };
    if (url.includes('/api/v1/jobs?')) return { body: [] };
    if (url.endsWith('/api/v1/deployments')) return { body: [] };
    return undefined;
  });

const wizard = (step: string, d = deployment('ex1')) => render(
  <MemoryRouter future={ROUTER_FUTURE}><DeploymentsProvider><BuildWizard deployment={d} step={step} /></DeploymentsProvider></MemoryRouter>,
);

describe('the builder', () => {
  it('starts on the shipped versions with their preflight', async () => {
    serve();
    wizard('base');
    expect(await screen.findByText('grayspace/2026.11')).toBeInTheDocument();
    expect(screen.getByText('134 routers')).toBeInTheDocument();
    expect(await screen.findByText('Ready to deploy')).toBeInTheDocument();
    expect(screen.getByText(/Nothing is deployed on ex1 yet/)).toBeInTheDocument();
  });

  it('previews a version: its map, and its ISPs with their routers', async () => {
    const router = (id: string, isp: string, location: string) => ({ type: 'Feature', id, geometry: { type: 'Point', coordinates: [0, 0] }, properties: { id, kind: 'router', isp, asn: 1, location } });
    serve((url) => (url.includes('/api/v1/core/preview?version=grayspace%2F2026.11&scope=full')
      ? { body: { type: 'FeatureCollection', features: [router('lum-chi', 'Lumen', 'Chicago'), router('lum-nyc', 'Lumen', 'New York'), router('ntt-tok', 'NTT', 'Tokyo')] } } : undefined));
    wizard('base');
    fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
    expect(await screen.findByTestId('preview-map')).toHaveTextContent('3 features');
    expect(screen.getByText('Lumen').closest('tr')!).toHaveTextContent('Lumen122');
    expect(screen.getByText(/2 ISPs · 3 routers · 0 links/)).toBeInTheDocument();
  });

  it('saves the draft when moving on, with the chosen enclaves and their prefixes', async () => {
    const calls = serve();
    wizard('enclaves');
    fireEvent.click(await screen.findByLabelText('Build meridian-bank'));
    fireEvent.change(screen.getByLabelText('meridian-bank prefix'), { target: { value: 'bank' } });
    fireEvent.click(screen.getByRole('button', { name: 'Next: DNS' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/api/v1/draft') && c.init?.method === 'PUT')).toBe(true));
    const put = calls.find((c) => c.url.endsWith('/api/v1/draft') && c.init?.method === 'PUT')!;
    expect(JSON.parse(String(put.init?.body))).toEqual({ core: { version: 'grayspace/2026.11', scope: 'full', configure: true }, enclaves: [{ slug: 'meridian-bank', prefix: 'bank', version: null }] });
  });

  it('checks each enclave as the draft\'s instance, so the one already built is not a conflict', async () => {
    const calls = serve((url) => (url.endsWith('/api/v1/draft')
      ? { body: { core: { version: 'grayspace/2026.11', scope: 'full', configure: true }, enclaves: [{ slug: 'meridian-bank', prefix: 'mb', version: null }] } } : undefined));
    wizard('enclaves');
    const validations = () => calls.filter((c) => c.url.includes('/api/v1/enclaves/meridian-bank/validate')).map((c) => c.url);
    await waitFor(() => expect(validations().some((u) => u.includes('instance=mb'))).toBe(true));
    fireEvent.change(await screen.findByLabelText('meridian-bank prefix'), { target: { value: 'bank' } });
    await waitFor(() => expect(validations().some((u) => u.includes('instance=bank'))).toBe(true));
  });

  it('deploys from Review as one rollout', async () => {
    const calls = serve((url, init) => (url.endsWith('/api/v1/deploy') && init?.method === 'POST'
      ? { status: 202, body: { instance: null, job: { id: 'r1', kind: 'rollout', target: 'deployment', state: 'queued', stages: ['base', 'create:mb'], stage: null, created: new Date().toISOString(), children: [], stage_titles: {} } } }
      : url.endsWith('/api/v1/jobs/r1') ? { body: { id: 'r1', kind: 'rollout', target: 'deployment', state: 'running', stages: ['base', 'create:mb'], stage: 'base', created: new Date().toISOString(), started: new Date().toISOString(), children: [], result: { children: {} }, stage_titles: { base: 'Deploy or update the core', 'create:mb': 'Create instance mb' } } }
        : undefined));
    wizard('review');
    const button = await screen.findByRole('button', { name: 'Deploy the core and 1 enclave' });
    await act(async () => { fireEvent.click(button); });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/api/v1/deploy') && c.init?.method === 'POST')).toBe(true));
    expect(await screen.findByText('Create instance mb')).toBeInTheDocument();
  });

  it("shows a failed rollout only while retrying it would deploy the draft", async () => {
    const failed = { id: 'r0', kind: 'rollout', target: 'deployment', state: 'failed', stages: ['create:old'], stage: 'create:old', error: 'Instance old is destroyed',
      created: new Date().toISOString(), children: [], result: { children: {} }, stage_titles: { 'create:old': 'Create instance old' } };
    const plan = (resumable: string | null) => (url: string) => (url.includes('/api/v1/jobs?') ? { body: [failed] }
      : url.endsWith('/api/v1/jobs/r0') ? { body: failed }
        : url.endsWith('/api/v1/deploy/plan') ? { body: { core: { action: 'none', deployed: { name: 'grayspace/2026.11', scope: 'full' }, version: 'grayspace/2026.11', scope: 'full', configure: true },
          instances: [], unmanaged: [], problems: [], stages: [], changes: false, capacity: CAPS.capacity, resumable_rollout: resumable } } : undefined);
    serve(plan(null));
    const view = wizard('review');
    expect(await screen.findByText('Nothing to change.')).toBeInTheDocument();
    expect(screen.queryByText('Instance old is destroyed')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Retry/ })).not.toBeInTheDocument();
    view.unmount();

    serve(plan('r0'));
    wizard('review');
    expect(await screen.findByRole('button', { name: 'Retry from create:old' })).toBeInTheDocument();
    expect(screen.getByText('Instance old is destroyed')).toBeInTheDocument();
  });

  it('deletes an enclave from the library and the draft', async () => {
    const calls = serve((url, init) => (init?.method === 'DELETE' ? { status: 204 }
      : url.endsWith('/api/v1/enclaves') ? { body: [{ slug: 'meridian-bank', kind: 'slice', description: 'A bank', versions: [{ version: 'sha256:1', created: '' }], instances: [] }] }
        : undefined));
    jest.spyOn(window, 'confirm').mockReturnValue(true);
    wizard('enclaves');
    fireEvent.click(await screen.findByLabelText('Build meridian-bank'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete meridian-bank' })); });
    expect(calls.some((c) => c.url.endsWith('/api/v1/enclaves/meridian-bank') && c.init?.method === 'DELETE')).toBe(true);
    expect(screen.queryByLabelText('Build meridian-bank')).not.toBeInTheDocument();
  });

  it("can't delete an enclave with instances", async () => {
    serve((url) => (url.endsWith('/api/v1/enclaves') ? { body: [{ slug: 'meridian-bank', kind: 'slice', description: '', versions: [], instances: ['mb'] }] } : undefined));
    wizard('enclaves');
    expect(await screen.findByRole('button', { name: 'Delete meridian-bank' })).toBeDisabled();
  });

  it('is read-only with use access', async () => {
    serve();
    wizard('enclaves', deployment('ex1', { my_access: ['read', 'operate'] }));
    expect(await screen.findByLabelText('Build meridian-bank')).toBeDisabled();
    expect(screen.getByText(/building needs build access/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '＋ New enclave' })).not.toBeInTheDocument();
  });
});
