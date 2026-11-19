import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { apiService } from '../services/api';
import { fakeServer } from '../testing';
import { PresetModal } from './PresetModal';

const CHANGES = {
  preset: 'slow-links', base: 'grayspace/2026.11', baseline: 'slow-links', count: 2,
  changes: [{ kind: 'traffic', key: 'a:b', summary: 'a:b shaped: latency_ms 80', by: 'uma', at: new Date().toISOString() }],
};
const LIST = [
  { name: 'slow-links', description: 'Transatlantic at 80 ms', owner: { kind: 'base', ref: 'grayspace/2026.11' }, created: new Date().toISOString(),
    created_by: 'bob', actions: 2, kinds: { 'link.condition': 2 }, problems: [] },
  { name: 'dns-outage', description: '', owner: { kind: 'base', ref: 'grayspace/2026.11' }, created: new Date().toISOString(),
    created_by: null, actions: 1, kinds: { 'dns.record.set': 1 }, problems: ['No DNS server dns-x'] },
];

beforeEach(() => apiService.setDeployment('ex1'));
afterEach(() => apiService.setDeployment(null));

const server = () => fakeServer((url, init) => {
  if (url.endsWith('/api/v1/changes')) return { body: CHANGES };
  if (url.endsWith('/api/v1/base/presets') && !init?.method) return { body: LIST };
  if (url.includes('/apply')) return { body: { success: true, message: 'Applied dns-outage', applied: { 'dns.record.set': 1 }, warnings: [], errors: [] } };
  if (url.endsWith('/api/v1/base/presets') && init?.method === 'POST') return { body: { ...LIST[0], name: 'mine', actions: [] } };
  return undefined;
});

describe('the preset box', () => {
  it('shows the active preset, the changes since it, and the base version\'s presets', async () => {
    server();
    render(<PresetModal onClose={() => undefined} onChanged={() => undefined} canOperate canBuild />);
    expect(await screen.findByText('a:b shaped: latency_ms 80')).toBeInTheDocument();
    expect(screen.getByText('Presets for grayspace/2026.11')).toBeInTheDocument();
    expect(screen.getByText('2 links')).toBeInTheDocument();
    expect(screen.getByText(/1 target this deployment doesn't have/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply again' })).toBeInTheDocument();
  });

  it('applies a preset after saying what it resets, and saves one', async () => {
    const calls = server();
    const changed = jest.fn();
    window.confirm = jest.fn(() => true);
    render(<PresetModal onClose={() => undefined} onChanged={changed} canOperate canBuild />);
    await screen.findByText('dns-outage');
    fireEvent.click(screen.getAllByRole('button', { name: 'Apply' })[1]);
    await screen.findByText('Applied dns-outage');
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('resets the 1 change made since slow-links'));
    expect(calls.some((c) => c.url.endsWith('/deployments/ex1/api/v1/base/presets/dns-outage/apply') && c.init?.method === 'POST')).toBe(true);
    fireEvent.change(screen.getByLabelText('Preset name'), { target: { value: 'My Preset' } });
    expect((screen.getByLabelText('Preset name') as HTMLInputElement).value).toBe('my-preset');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === 'POST' && c.url.endsWith('/api/v1/base/presets'))).toBe(true));
    expect(changed).toHaveBeenCalled();
  });

  it('lets a user apply but not save or delete', async () => {
    server();
    render(<PresetModal onClose={() => undefined} onChanged={() => undefined} canOperate canBuild={false} />);
    await screen.findByText('dns-outage');
    expect(screen.queryByText('Save as a preset')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Apply' }).length).toBe(2);
  });
});
