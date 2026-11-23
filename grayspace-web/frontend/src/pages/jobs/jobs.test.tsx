import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ROUTER_FUTURE } from '../../shell/router';
import { apiService } from '../../services/api';
import { deployment, fakeServer } from '../../testing';
import { JobRecord } from '../../types/slices';
import { OverviewPage } from '../overview/OverviewPage';
import { useTopologyStore } from '../../stores/topologyStore';
import { stageStatuses, stripAnsi } from './format';
import { JobDetail } from './JobDetail';

const job = (extra: Partial<JobRecord>): JobRecord => ({
  id: 'j1', kind: 'create_instance', target: 'mb', slice: null, state: 'running', stages: ['validate', 'allocate', 'apply', 'ready'],
  stage: 'apply', attempt: 'apply attempt 2/6', created: new Date().toISOString(), started: new Date().toISOString(), finished: null,
  error: null, cancel_requested: false, resume_from: null, retry_of: null, result: null, by: 'blake', parent: null, children: [],
  stage_titles: { validate: 'Validate the spec', allocate: 'Allocate addresses', apply: 'Apply the instance', ready: 'Write the manifest' }, ...extra,
});

beforeEach(() => apiService.setDeployment('ex1'));
afterEach(() => apiService.setDeployment(null));

describe('stage statuses', () => {
  it('follow the job', () => {
    expect(stageStatuses(job({}))).toEqual(['done', 'done', 'current', 'pending']);
    expect(stageStatuses(job({ state: 'failed' }))).toEqual(['done', 'done', 'failed', 'pending']);
    expect(stageStatuses(job({ state: 'succeeded' }))).toEqual(['done', 'done', 'done', 'done']);
    // A retry resumes at apply: the stages before it were done by the job it retried
    expect(stageStatuses(job({ resume_from: 'apply', stage: 'ready' }))).toEqual(['skipped', 'skipped', 'done', 'current']);
  });
});

const renderDetail = (record: JobRecord, canBuild: boolean, children: JobRecord[] = []) => {
  const calls = fakeServer((url) => {
    if (url.includes('/api/v1/jobs?')) return { body: children };
    if (url.endsWith(`/api/v1/jobs/${record.id}`)) return { body: record };
    if (url.includes('/log')) return { body: 'line 1\n' };
    if (url.endsWith('/retry')) return { status: 202, body: { instance: null, job: { ...record, id: 'j2' } } };
    return undefined;
  });
  const retried = jest.fn();
  render(<MemoryRouter future={ROUTER_FUTURE}><JobDetail deploymentId="ex1" jobId={record.id} canBuild={canBuild} onRetried={retried} /></MemoryRouter>);
  return { calls, retried };
};

describe('the log', () => {
  it('drops terminal color codes', () => {
    expect(stripAnsi('\x1b[0m\x1b[1mrouters["lum-for"]: Creating...\x1b[0m')).toBe('routers["lum-for"]: Creating...');
  });
});

describe('a job', () => {
  it("follows the log's end until the reader scrolls up, and again when they scroll back down", async () => {
    let write: (text: string) => void = () => undefined;
    const stream = jest.spyOn(apiService, 'streamJobLog').mockImplementation(async (_id, onText) => { write = onText; });
    renderDetail(job({}), true);
    await screen.findByText('Apply the instance');
    const log = document.querySelector('.gs-log') as HTMLElement;
    // A chunk of output far taller than the view, as Terraform's and Ansible's are
    let height = 400;
    Object.defineProperty(log, 'clientHeight', { configurable: true, get: () => 400 });
    Object.defineProperty(log, 'scrollHeight', { configurable: true, get: () => height });
    Object.defineProperty(log, 'scrollTop', { configurable: true, writable: true, value: 0 });
    const chunk = (lines: number) => act(() => { height += lines * 15; write('x\n'.repeat(lines)); });

    chunk(300);
    expect(log.scrollTop).toBe(height);
    chunk(300);
    expect(log.scrollTop).toBe(height);

    log.scrollTop = 100;
    fireEvent.scroll(log);
    chunk(300);
    expect(log.scrollTop).toBe(100);

    log.scrollTop = height - 400;
    fireEvent.scroll(log);
    chunk(300);
    expect(log.scrollTop).toBe(height);
    stream.mockRestore();
  });

  it('shows each stage with what it does, and who ran it', async () => {
    renderDetail(job({}), true);
    expect(await screen.findByText('Apply the instance')).toBeInTheDocument();
    expect(screen.getByText(/apply attempt 2\/6/)).toBeInTheDocument();
    expect(screen.getByText(/by blake/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('retries from the failed stage, for those who build', async () => {
    const { calls, retried } = renderDetail(job({ state: 'failed', error: 'Ansible failed' }), true);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry from apply' }));
    await screen.findByText('Ansible failed');
    await new Promise((r) => setTimeout(r, 0));
    expect(calls.some((c) => c.url === '/deployments/ex1/api/v1/jobs/j1/retry' && c.init?.method === 'POST')).toBe(true);
    expect(retried).toHaveBeenCalledWith('j2');
  });

  it('has no buttons for those who only use the deployment', async () => {
    renderDetail(job({ state: 'failed' }), false);
    await screen.findByText('Apply the instance');
    expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
  });

  it("is a rollout's: retried through the rollout", async () => {
    renderDetail(job({ state: 'failed', parent: 'r1' }), true);
    expect(await screen.findByText(/retry the deploy/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument();
  });

  const secure = (on: boolean) => Object.defineProperty(window, 'isSecureContext', { value: on, configurable: true });
  // jsdom has no execCommand: define it for the fallback's tests
  const execCommand = (works: boolean) => {
    const fn = jest.fn().mockReturnValue(works);
    Object.defineProperty(document, 'execCommand', { value: fn, configurable: true });
    return fn;
  };
  afterEach(() => secure(true));

  it('says where it failed, and copies the details for an admin', async () => {
    secure(true);
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderDetail(job({ state: 'failed', stage: 'allocate', error: 'No free /24 left' }), true);
    const copy = await screen.findByRole('button', { name: 'Copy details for your admin' });
    await act(async () => { fireEvent.click(copy); });
    expect(writeText.mock.calls[0][0]).toContain('Error: No free /24 left');
    expect(writeText.mock.calls[0][0]).toContain('State: Failed: Allocate addresses');
    expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument();
  });

  it('copies over plain HTTP, where the Clipboard API is missing', async () => {
    secure(false);
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    const exec = execCommand(true);
    renderDetail(job({ state: 'failed', stage: 'allocate', error: 'No free /24 left' }), true);
    const copy = await screen.findByRole('button', { name: 'Copy details for your admin' });
    await act(async () => { fireEvent.click(copy); });
    expect(exec).toHaveBeenCalledWith('copy');
    expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument();
  });

  it("shows the details to copy by hand when the browser won't copy", async () => {
    secure(false);
    execCommand(false);
    renderDetail(job({ state: 'failed', stage: 'allocate', error: 'No free /24 left' }), true);
    const copy = await screen.findByRole('button', { name: 'Copy details for your admin' });
    await act(async () => { fireEvent.click(copy); });
    const box = screen.getByLabelText('Text to copy') as HTMLTextAreaElement;
    expect(box.value).toContain('Error: No free /24 left');
    expect(screen.getByText(/press Ctrl\+C/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Copied' })).not.toBeInTheDocument();
  });

  it('as a summary, says which step it is on and keeps the steps and log behind Details', async () => {
    fakeServer((url) => (url.endsWith('/api/v1/jobs/j1') ? { body: job({}) } : url.includes('/log') ? { body: 'line 1\n' } : undefined));
    render(<MemoryRouter future={ROUTER_FUTURE}><JobDetail summary deploymentId="ex1" jobId="j1" canBuild onRetried={jest.fn()} /></MemoryRouter>);
    expect(await screen.findByText('Step 3 of 4: Apply the instance')).toBeInTheDocument();
    expect(screen.getByText('Details')).toBeInTheDocument();
    expect(screen.queryByText('Log')).not.toBeInTheDocument();
  });

  it("lists a rollout's steps and the jobs that ran them", async () => {
    const child = job({ id: 'c1', kind: 'deploy_base', target: 'base', state: 'succeeded', stages: ['parse'], stage: 'parse', parent: 'r1' });
    renderDetail(job({ id: 'r1', kind: 'rollout', target: 'deployment', stages: ['base', 'create:mb'], stage: 'create:mb', children: ['c1'],
      result: { children: { base: 'c1' } }, stage_titles: { base: 'Deploy or update the core', 'create:mb': 'Create instance mb' } }), true, [child]);
    expect(await screen.findByText('Create instance mb')).toBeInTheDocument();
    expect(await screen.findByText('waiting')).toBeInTheDocument();
    expect(screen.getByText('succeeded')).toBeInTheDocument();
  });
});

describe('the overview', () => {
  const base = { version: { name: 'grayspace/2026.11', csv_sha256: 'x', scope: 'full' }, status: 'degraded',
    routers: { expected: 134, running: 133, not_running: ['lum-chi'] }, bgp: { total: 10, established: 9, not_established: [{ router: 'ntt-tok', peer_address: '1.2.3.4', state: 'down' }] } };
  const serve = () => fakeServer((url) => {
    if (url.endsWith('/api/v1/core')) return { body: base };
    if (url.endsWith('/api/v1/instances')) return { body: [] };
    if (url.includes('/api/v1/jobs')) return { body: [] };
    return undefined;
  });

  it("shows the base's health and, for owners, how to change or destroy it", async () => {
    serve();
    render(<MemoryRouter future={ROUTER_FUTURE}><OverviewPage deployment={deployment('ex1')} /></MemoryRouter>);
    expect(await screen.findByText("lum-chi isn't running")).toBeInTheDocument();
    expect(screen.getByText('ntt-tok: BGP to 1.2.3.4 is down')).toBeInTheDocument();
    expect(screen.getByText(/133\/134 routers running · 9\/10 BGP sessions up/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expect(screen.getByRole('menuitem', { name: 'Re-run configure' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Destroy deployment…' }));
    expect(screen.getByRole('button', { name: 'Destroy' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/to confirm/), { target: { value: 'ex1' } });
    expect(screen.getByRole('button', { name: 'Destroy' })).toBeEnabled();
  });

  it('offers users neither', async () => {
    serve();
    render(<MemoryRouter future={ROUTER_FUTURE}><OverviewPage deployment={deployment('ex1', { my_access: ['read', 'operate'] })} /></MemoryRouter>);
    expect(await screen.findByText("lum-chi isn't running")).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Destroy deployment…' })).not.toBeInTheDocument();
  });
});

describe("the overview's base details", () => {
  it('break the base down by ISP, and list its core services', async () => {
    const base = { version: { name: 'grayspace/2026.11', csv_sha256: 'x', scope: 'full' }, status: 'degraded',
      routers: { expected: 2, running: 1, not_running: ['lum-chi'] }, bgp: { total: 2, established: 1, not_established: [{ router: 'ntt-tok', peer_address: '1.2.3.4', state: 'down' }] } };
    fakeServer((url) => (url.endsWith('/api/v1/core') ? { body: base } : url.endsWith('/api/v1/instances') || url.includes('/api/v1/jobs') ? { body: [] } : undefined));
    const store = useTopologyStore.getState();
    store.setISPs([{ name: 'Lumen', asn: 3356, public_block: '' }, { name: 'NTT', asn: 2914, public_block: '' }]);
    store.setRouters([{ hostname: 'lum-chi', isp_name: 'Lumen' }, { hostname: 'ntt-tok', isp_name: 'NTT' }] as never);
    store.setDNSServers([{ hostname: 'dns-google', role: 'resolver' }] as never);
    render(<MemoryRouter future={ROUTER_FUTURE}><OverviewPage deployment={deployment('ex1')} /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Details' }));
    const lumen = screen.getByText('Lumen').closest('tr')!;
    expect(lumen).toHaveTextContent('0/1');
    expect(screen.getByText('NTT').closest('tr')!).toHaveTextContent('1/1');
    expect(screen.getByText('dns-google')).toBeInTheDocument();
    act(() => store.reset());
  });
});

describe('a rollout', () => {
  it("shows the steps it runs itself as its own", async () => {
    renderDetail(job({ id: 'r2', kind: 'rollout', target: 'deployment', state: 'succeeded', stages: ['preset', 'player'], stage: 'player', children: [],
      result: { children: {} }, stage_titles: { preset: 'Apply the core preset', player: 'Apply the Player view' } }), true, []);
    expect(await screen.findByText('Apply the Player view')).toBeInTheDocument();
    expect(screen.queryByText('waiting')).not.toBeInTheDocument();
  });
});
