/**
 * The deployments you own or are a member of (admins: every one, with Mine to narrow it), and
 * New deployment for builders and admins.
 */
import React, { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../../auth/AuthProvider';
import { deploymentState, isLive, STATE_LABELS } from '../../shell/deployments';
import { useDeployments } from '../../shell/DeploymentsProvider';
import { landing } from '../../shell/sections';
import { Deployment } from '../../types/control';
import { NewDeploymentModal } from './NewDeploymentModal';

const accessOf = (d: Deployment, username: string | undefined, admin: boolean): string | null => {
  if (d.owner && d.owner === username) return 'owner';
  const member = d.members.find((m) => m.user === username);
  if (member) return `${member.access} access`;
  return admin ? 'admin' : null;
};

const when = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 90) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
};

const Card: React.FC<{ d: Deployment; username?: string; admin: boolean }> = ({ d, username, admin }) => {
  const state = deploymentState(d);
  const [tone, label] = STATE_LABELS[state];
  const job = d.summary?.active_job;
  const access = accessOf(d, username, admin);
  const instances = (d.summary?.instances_live ?? 0) + (d.summary?.instances_failed ?? 0);
  return (
    <Link className="gs-card" to={`/d/${d.id}/${landing(isLive(d))}`}>
      <div className="gs-card-name">
        <span className={`gs-dot ${tone}`} />
        <span className="gs-mono">{d.id}</span>
        {d.name !== d.id && <span style={{ fontWeight: 500, fontSize: 13 }}>{d.name}</span>}
        <span className="gs-spacer" />
        <span className={`gs-pill ${tone}`}>{state === 'deploying' ? '⟳ ' : ''}{label}</span>
      </div>
      <div className="gs-muted" style={{ margin: '6px 0' }}>
        {d.base?.version?.name || 'no base yet'}{d.base?.scope ? ` · ${d.base.scope}` : ''}{d.proxmox.node ? ` · ${d.proxmox.node}` : ''}
      </div>
      <div className="gs-row" style={{ flexWrap: 'wrap', gap: 5 }}>
        {d.base?.routers ? <span className="gs-tag">{d.base.routers} routers</span> : null}
        <span className="gs-tag">{instances} enclave instance{instances === 1 ? '' : 's'}</span>
        {d.summary?.instances_failed ? <span className="gs-pill bad">{d.summary.instances_failed} failed</span> : null}
        {d.owner && <span className="gs-tag">owner {d.owner}</span>}
        {access && <span className={`gs-pill ${access === 'use access' ? '' : 'info'}`}>you: {access}</span>}
      </div>
      {job && <div className="gs-muted" style={{ marginTop: 8, fontSize: 11 }}>⟳ {job.kind}{job.stage ? `: ${job.stage}` : ''}{job.attempt ? ` (${job.attempt})` : ''}</div>}
      {d.warnings.map((w) => <div key={w} className="gs-muted" style={{ marginTop: 6, fontSize: 11 }}>⚠ {w}</div>)}
      <div className="gs-muted" style={{ fontSize: 11, marginTop: 8 }}>{d.summary?.updated ? `updated ${when(d.summary.updated)}` : `created ${when(d.created)}`}</div>
    </Link>
  );
};

export const DeploymentsHome: React.FC = () => {
  const { me, can } = useAuth();
  const { deployments, loaded, error, refresh } = useDeployments();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const admin = can('deployments:all');
  const [all, setAll] = useState(false);
  const username = me?.user?.username;
  const mine = (d: Deployment) => d.owner === username || d.members.some((m) => m.user === username);
  const shown = admin && !all ? deployments.filter(mine) : deployments;
  const creating = params.get('new') === '1';

  return (
    <div className="gs-page">
      <div className="gs-row" style={{ marginBottom: 12 }}>
        <div>
          <h2 className="gs-title">Deployments</h2>
          <div className="gs-subtitle" style={{ margin: 0 }}>{admin && all ? 'Every deployment on this server.' : 'Deployments you own or are a member of.'}</div>
        </div>
        <span className="gs-spacer" />
        {admin && (
          <div className="gs-seg">
            <button className={all ? '' : 'on'} onClick={() => setAll(false)}>Mine</button>
            <button className={all ? 'on' : ''} onClick={() => setAll(true)}>All</button>
          </div>
        )}
      </div>
      {error && <div className="gs-banner bad">{error}</div>}
      <div className="gs-grid3">
        {shown.map((d) => <Card key={d.id} d={d} username={username} admin={admin} />)}
        {can('deployments:create') && (
          <button className="gs-card new" onClick={() => setParams({ new: '1' })}>＋ New deployment</button>
        )}
      </div>
      {loaded && shown.length === 0 && (
        <div className="gs-empty">
          {admin && !all && deployments.length ? 'No deployments yet.' :
            can('deployments:create') ? 'No deployments yet.' : 'No deployments yet. Ask a builder or an admin to add you to one.'}
        </div>
      )}
      {creating && (
        <NewDeploymentModal
          onClose={() => setParams({})}
          onCreated={async (d) => { await refresh(); navigate(`/d/${d.id}/build`); }}
        />
      )}
    </div>
  );
};
