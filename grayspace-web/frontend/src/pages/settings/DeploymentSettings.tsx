/**
 * A deployment's settings and who may use it: its name, where it runs (while nothing is deployed),
 * its members (PUT /deployments/{id}/members), and removing it. For its owner and admins.
 */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { control } from '../../api/control';
import { apiService } from '../../services/api';
import { canIn } from '../../auth/AuthProvider';
import { deploymentState, isLive } from '../../shell/deployments';
import { useDeployments } from '../../shell/DeploymentsProvider';
import { PlacementPicker } from '../../components/PlacementPicker';
import { Tip } from '../../components/Tip';
import { Deployment, DeploymentOptions, DirectoryEntry, Member, Placement, Platform } from '../../types/control';

export const DeploymentSettings: React.FC<{ deployment: Deployment }> = ({ deployment }) => {
  const { refresh } = useDeployments();
  const navigate = useNavigate();
  const [name, setName] = useState(deployment.name);
  const [description, setDescription] = useState(deployment.description);
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [placement, setPlacement] = useState<Placement | null>(deployment.placement ?? null);
  const [options, setOptions] = useState<DeploymentOptions>(deployment.options);
  const [members, setMembers] = useState<Member[]>(deployment.members);
  const [directory, setDirectory] = useState<DirectoryEntry[]>([]);
  const [adding, setAdding] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const deployed = isLive(deployment) || deploymentState(deployment) === 'deploying' || Boolean(deployment.summary?.instances_live);
  const id = deployment.id;

  useEffect(() => {
    control.directory().then(setDirectory).catch(() => setDirectory([]));
    control.platform().then(setPlatform).catch(() => setPlatform(null));
  }, []);
  useEffect(() => setMembers(deployment.members), [deployment.members]);
  // The Player view, once a rollout has made it
  const [playerUrl, setPlayerUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!deployment.options.player) return;
    apiService.getPlayerView().then((v) => setPlayerUrl(v.url)).catch(() => setPlayerUrl(null));
  }, [deployment.options.player]);

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setError(null);
    setMessage(null);
    try {
      await fn();
      await refresh();
      setMessage(done);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const roleOf = (u: string) => directory.find((d) => d.username === u)?.role;
  const saveMembers = (next: Member[]) => act(async () => setMembers((await control.setMembers(id, next)).members), 'Members saved');
  const candidates = directory.filter((d) => d.username !== deployment.owner && !members.some((m) => m.user === d.username) && d.role !== 'admin');

  return (
    <div className="gs-page gs-dark">
      <h2 className="gs-title">Settings &amp; access</h2>
      <div className="gs-subtitle"><span className="gs-mono">{id}</span> · owner {deployment.owner || 'none (made with the service token)'}</div>
      {message && <div className="gs-banner info">{message}</div>}
      {error && <div className="gs-banner bad">{error}</div>}
      <div className="gs-grid2" style={{ alignItems: 'start' }}>
        <div>
          <div className="gs-box">
            <h3>Deployment</h3>
            <div className="gs-grid2">
              <div className="gs-field"><label htmlFor="s-name">Name</label><input id="s-name" value={name} onChange={(e) => setName(e.target.value)} /></div>
              <div className="gs-field"><label htmlFor="s-id">ID</label><input id="s-id" className="gs-mono" value={id} disabled /></div>
            </div>
            <div className="gs-field"><label htmlFor="s-description">Description</label><input id="s-description" value={description} onChange={(e) => setDescription(e.target.value)} /></div>
            <div className="gs-actions" style={{ marginTop: 0 }}>
              <button className="gs-btn sm" disabled={name === deployment.name && description === deployment.description}
                onClick={() => act(() => control.updateDeployment(id, { name, description }), 'Saved')}>Save</button>
            </div>
          </div>
          <div className="gs-box">
            <h3>Platform {deployed && <span className="gs-pill">locked while deployed</span>}</h3>
            {deployment.kind === 'external' ? (
              <div className="gs-muted" style={{ marginBottom: 8 }}>
                An adopted tree: its settings are its own environment file's{deployment.proxmox.host_addr ? ` (${deployment.proxmox.host_addr}, node ${deployment.proxmox.node || '?'})` : ''}.
              </div>
            ) : (
              <PlacementPicker platform={platform} placement={placement} options={options} disabled={deployed} idPrefix="s"
                onChange={(p, o) => { setPlacement(p); setOptions(o); }} />
            )}
            {playerUrl && <a className="gs-btn ghost sm" style={{ marginTop: 6, display: 'inline-block' }} href={playerUrl} target="_blank" rel="noreferrer">Open in Player ↗</a>}
            {deployment.kind !== 'external' && (
              <div className="gs-actions" style={{ marginTop: 6 }}>
                <button className="gs-btn sm" disabled={deployed || (JSON.stringify(placement) === JSON.stringify(deployment.placement ?? null)
                  && Boolean(options.player) === Boolean(deployment.options.player) && (options.player_connection ?? null) === (deployment.options.player_connection ?? null))}
                  onClick={() => act(() => control.updateDeployment(id, { placement: placement ?? undefined, options: { player: Boolean(options.player), player_connection: options.player_connection ?? null } }),
                    'Saved; its backend restarts with them')}>Save</button>
              </div>
            )}
          </div>
          <div className="gs-box">
            <h3>Remove</h3>
            <div className="gs-row">
              {canIn(deployment, 'build') && <button className="gs-btn ghost sm" onClick={() => act(() => control.restartDeployment(id), 'Its backend is restarting')}>Restart its backend</button>}
              <span className="gs-spacer" />
              {canIn(deployment, 'delete') && (
                <button className="gs-btn bad sm" disabled={deployed} title={deployed ? 'Destroy it first, from the Overview' : undefined}
                  onClick={() => window.confirm(`Remove ${id}? Its draft, enclave library and jobs go too.`) && act(async () => { await control.deleteDeployment(id); navigate('/'); }, 'Removed')}>
                  Remove the deployment
                </button>
              )}
            </div>
            {deployed && <div className="gs-muted" style={{ marginTop: 6 }}>Something is deployed: destroy it from the Overview before removing the deployment.</div>}
          </div>
        </div>
        <div className="gs-box">
          <h3>Members</h3>
          <table className="gs-table">
            <thead><tr><th>User</th><th>Role</th><th>Access <Tip text="Build: change and deploy it (builders only). Use: view it and operate it. Admins see every deployment." /></th><th /></tr></thead>
            <tbody>
              {deployment.owner && <tr><td>{deployment.owner}</td><td><span className="gs-pill">{roleOf(deployment.owner) || ''}</span></td><td><span className="gs-pill info">owner</span></td><td /></tr>}
              {members.map((m) => (
                <tr key={m.user}>
                  <td>{m.user}</td>
                  <td><span className="gs-pill">{roleOf(m.user) || '?'}</span></td>
                  <td>
                    <select className="gs-input" style={{ width: 110 }} aria-label={`${m.user} access`} value={m.access}
                      onChange={(e) => saveMembers(members.map((x) => (x.user === m.user ? { ...x, access: e.target.value as Member['access'] } : x)))}>
                      <option value="build" disabled={roleOf(m.user) === 'user'}>build</option>
                      <option value="use">use</option>
                    </select>
                  </td>
                  <td style={{ textAlign: 'right' }}><button className="gs-btn ghost sm" onClick={() => saveMembers(members.filter((x) => x.user !== m.user))}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="gs-row" style={{ marginTop: 8 }}>
            <select className="gs-input" value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="Add a member">
              <option value="">Add someone…</option>
              {candidates.map((d) => <option key={d.username} value={d.username}>{d.name !== d.username ? `${d.name} (${d.username})` : d.username} · {d.role}</option>)}
            </select>
            <button className="gs-btn sm" disabled={!adding} onClick={() => { saveMembers([...members, { user: adding, access: 'use' }]); setAdding(''); }}>Add with use access</button>
          </div>
        </div>
      </div>
    </div>
  );
};
