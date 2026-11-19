import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { fakeServer, PLATFORM } from '../../testing';
import { NewDeploymentModal } from './NewDeploymentModal';

const TWO_CRUCIBLES = { ...PLATFORM, crucible: [...PLATFORM.crucible, { ...PLATFORM.crucible[0], id: 'cru2', name: 'Other' }] };

const open = (platform: unknown = PLATFORM) => {
  const calls = fakeServer((url, init) => {
    if (url.endsWith('/api/v1/platform')) return { body: platform };
    if (url.endsWith('/api/v1/deployments') && init?.method === 'POST') return { status: 201, body: { id: 'ex2' } };
    return undefined;
  });
  const created = jest.fn();
  render(<NewDeploymentModal onClose={() => undefined} onCreated={created} />);
  return { calls, created };
};

describe('a new deployment', () => {
  it('runs on a connection and node, with a Player view if asked', async () => {
    const { calls, created } = open();
    expect(await screen.findByLabelText('Proxmox connection')).toHaveValue('site');
    expect(screen.getByLabelText('Node')).toHaveValue('pve1');
    fireEvent.change(screen.getByLabelText('ID'), { target: { value: 'ex2' } });
    fireEvent.change(screen.getByLabelText('Node'), { target: { value: 'pve2' } });
    fireEvent.click(screen.getByLabelText(/Create a Crucible Player view/));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create and open the builder' })); });
    const post = calls.find((c) => c.init?.method === 'POST')!;
    expect(JSON.parse(String(post.init?.body))).toEqual({ id: 'ex2', description: '', placement: { connection: 'site', node: 'pve2' },
      options: { player: true, player_connection: 'cru' } });
    expect(created).toHaveBeenCalled();
  });

  it('asks which Crucible when there are two', async () => {
    open(TWO_CRUCIBLES);
    fireEvent.change(await screen.findByLabelText('ID'), { target: { value: 'ex2' } });
    fireEvent.click(screen.getByLabelText(/Create a Crucible Player view/));
    expect(screen.getByRole('button', { name: 'Create and open the builder' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Crucible connection'), { target: { value: 'cru2' } });
    expect(screen.getByRole('button', { name: 'Create and open the builder' })).toBeEnabled();
  });

  it('says to ask an admin when there are no connections', async () => {
    open({ proxmox: [], crucible: [], default_proxmox: null });
    expect(await screen.findByText(/ask an admin to add one/)).toBeInTheDocument();
  });
});
