/**
 * POST /api/v1/deployments: you become its owner. It runs on a Proxmox connection and node an
 * admin defined (Admin > Platform), and may have a Crucible Player view.
 */
import React, { useEffect, useState } from 'react';
import { control } from '../../api/control';
import { defaultPlacement, PlacementPicker } from '../../components/PlacementPicker';
import { CreateDeployment, Deployment, DeploymentOptions, Placement, Platform } from '../../types/control';

const ID = /^[a-z][a-z0-9]{0,3}$/;

export const NewDeploymentModal: React.FC<{ onClose: () => void; onCreated: (d: Deployment) => void }> = ({ onClose, onCreated }) => {
  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [placement, setPlacement] = useState<Placement | null>(null);
  const [options, setOptions] = useState<DeploymentOptions>({ player: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idProblem = id && !ID.test(id) ? '1 to 4 lowercase letters and digits, starting with a letter' : null;

  useEffect(() => {
    control.platform().then((p) => {
      setPlatform(p);
      setPlacement(defaultPlacement(p));
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body: CreateDeployment = { id, name: name || undefined, description, options };
    if (placement) body.placement = placement;
    try {
      onCreated(await control.createDeployment(body));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };
  const playerUnchosen = Boolean(options.player && (platform?.crucible.length ?? 0) > 1 && !options.player_connection);

  return (
    <div className="gs-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="gs-modal" onSubmit={submit}>
        <h3>New deployment</h3>
        <div className="gs-grid2">
          <div className="gs-field">
            <label htmlFor="dep-id">ID</label>
            <input id="dep-id" className="gs-mono" value={id} autoFocus onChange={(e) => setId(e.target.value.toLowerCase())} placeholder="ex2" />
            {idProblem && <div className="gs-hint">{idProblem}</div>}
          </div>
          <div className="gs-field">
            <label htmlFor="dep-name">Name</label>
            <input id="dep-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Exercise Bravo" />
          </div>
        </div>
        <div className="gs-field">
          <label htmlFor="dep-description">Description</label>
          <input id="dep-description" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <PlacementPicker platform={platform} placement={placement} options={options} idPrefix="dep"
          onChange={(p, o) => { setPlacement(p); setOptions(o); }} />
        {error && <div className="gs-banner bad" style={{ marginTop: 10 }}>{error}</div>}
        <div className="gs-actions">
          <button type="button" className="gs-btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="gs-btn" disabled={busy || !id || Boolean(idProblem) || !placement || playerUnchosen}>{busy ? 'Creating…' : 'Create and open the builder'}</button>
        </div>
      </form>
    </div>
  );
};
