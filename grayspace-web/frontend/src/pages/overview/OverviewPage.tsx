/**
 * How the deployment is doing: its base (routers, BGP, and per ISP and core service behind Details),
 * its enclave instances and its jobs, with what to do about them. Only this deployment's resources:
 * the Proxmox node is Admin's.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { canIn } from '../../auth/AuthProvider';
import { TERRAIN_EVENT } from '../../hooks/useTerrainEvents';
import { apiService } from '../../services/api';
import { Deployment } from '../../types/control';
import { useTopologyStore } from '../../stores/topologyStore';
import { DeployedBase, InstanceSummary, JobRecord } from '../../types/slices';
import { ago, kindLabel } from '../jobs/format';
import { StatePill } from '../jobs/JobDetail';

const INSTANCE_TONE: Record<string, string> = { ready: 'ok', failed: 'bad', destroying: 'warn', pending: '', provisioning: 'info', configuring: 'info' };
const BASE_TONE: Record<string, string> = { healthy: 'ok', degraded: 'warn', unknown: '', not_deployed: '' };

const count = (n: number | null | undefined, of: number | null | undefined, what: string) => `${n ?? '?'}/${of ?? '?'} ${what}`;

/** The base's ISPs and core services, from the topology the map uses, with what isn't running or up. */
const BaseDetails: React.FC<{ base: DeployedBase }> = ({ base }) => {
  const { routers, isps, dnsServers, caServers } = useTopologyStore();
  const down = new Set(base.routers.not_running);
  const bgpDown = new Map<string, number>();
  base.bgp.not_established.forEach((p) => bgpDown.set(p.router, (bgpDown.get(p.router) ?? 0) + 1));
  const rows = isps.map((isp) => {
    const mine = routers.filter((r) => r.isp_name === isp.name);
    return { isp, total: mine.length, running: mine.filter((r) => !down.has(r.hostname)).length,
      bgp: mine.reduce((n, r) => n + (bgpDown.get(r.hostname) ?? 0), 0) };
  });
  if (!routers.length) return <div className="gs-muted">Loading the topology…</div>;
  return (
    <div className="gs-grid2" style={{ alignItems: 'start', marginTop: 8 }}>
      <table className="gs-table">
        <thead><tr><th>ISP</th><th>ASN</th><th>Routers running</th><th>BGP sessions down</th></tr></thead>
        <tbody>{rows.map(({ isp, total, running, bgp }) => (
          <tr key={isp.name}>
            <td>{isp.name}</td><td className="gs-mono">{isp.asn}</td>
            <td style={{ color: running < total ? 'var(--gs-bad)' : undefined }}>{running}/{total}</td>
            <td style={{ color: bgp ? 'var(--gs-warn)' : undefined }}>{bgp || '—'}</td>
          </tr>
        ))}</tbody>
      </table>
      <div>
        <div className="gs-muted" style={{ marginBottom: 4 }}>DNS servers ({dnsServers.length})</div>
        <div className="gs-row" style={{ flexWrap: 'wrap', gap: 4 }}>{dnsServers.map((d) => <span key={d.hostname} className="gs-tag" title={d.role}>{d.hostname}</span>)}</div>
        <div className="gs-muted" style={{ margin: '10px 0 4px' }}>Certificate authorities ({caServers.length})</div>
        <div className="gs-row" style={{ flexWrap: 'wrap', gap: 4 }}>{caServers.map((c) => <span key={c.hostname} className="gs-tag" title={c.ca_name}>{c.ca_name}</span>)}</div>
      </div>
    </div>
  );
};

const DestroyModal: React.FC<{ deployment: Deployment; instances: number; onClose: () => void; onDone: (jobId: string) => void }> = ({
  deployment, instances, onClose, onDone,
}) => {
  const [typed, setTyped] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const destroy = async () => {
    setBusy(true);
    try {
      const jobs = await apiService.destroyBase(instances > 0);
      onDone(jobs[jobs.length - 1].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };
  return (
    <div className="gs-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="gs-modal">
        <h3>Destroy {deployment.id}?</h3>
        <p>
          This destroys {instances > 0 ? <>its {instances} enclave instance{instances === 1 ? '' : 's'}, then </> : null}
          the base: every router and core service on Proxmox. The deployment, its draft and its enclave library stay.
        </p>
        <div className="gs-field">
          <label htmlFor="confirm-id">Type <b className="gs-mono">{deployment.id}</b> to confirm</label>
          <input id="confirm-id" className="gs-mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
        </div>
        {error && <div className="gs-banner bad">{error}</div>}
        <div className="gs-actions">
          <button className="gs-btn ghost" onClick={onClose}>Keep it</button>
          <button className="gs-btn bad" disabled={typed !== deployment.id || busy} onClick={destroy}>{busy ? 'Queueing…' : 'Destroy'}</button>
        </div>
      </div>
    </div>
  );
};

export const OverviewPage: React.FC<{ deployment: Deployment }> = ({ deployment }) => {
  const navigate = useNavigate();
  const [base, setBase] = useState<DeployedBase | null>(null);
  const [instances, setInstances] = useState<InstanceSummary[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [destroying, setDestroying] = useState(false);
  const [details, setDetails] = useState(false);
  const [menu, setMenu] = useState(false);
  const [playerUrl, setPlayerUrl] = useState<string | null>(null);
  const id = deployment.id;
  useEffect(() => {
    if (!deployment.options.player) return;
    apiService.getPlayerView().then((v) => setPlayerUrl(v.url)).catch(() => setPlayerUrl(null));
  }, [deployment.options.player]);

  const load = useCallback(async () => {
    const [b, i, j] = await Promise.allSettled([apiService.getBase(), apiService.getInstances(), apiService.getJobs({ limit: 6 })]);
    if (b.status === 'fulfilled') setBase(b.value);
    if (i.status === 'fulfilled') setInstances(i.value);
    if (j.status === 'fulfilled') setJobs(j.value);
    const failed = [b, i, j].find((r) => r.status === 'rejected') as PromiseRejectedResult | undefined;
    setError(failed ? String(failed.reason?.message || failed.reason) : null);
  }, []);

  useEffect(() => {
    load();
    const onEvent = (e: Event) => {
      const type = (e as CustomEvent).detail?.type;
      if (type === 'job.update' || type === 'instance.update') load();
    };
    window.addEventListener(TERRAIN_EVENT, onEvent);
    const timer = setInterval(load, 15000);
    return () => {
      window.removeEventListener(TERRAIN_EVENT, onEvent);
      clearInterval(timer);
    };
  }, [load]);

  const configure = async () => {
    try {
      navigate(`/d/${id}/jobs/${(await apiService.configureBase()).job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const failedInstances = instances.filter((i) => i.state === 'failed');
  const routers = base?.routers;
  const bgp = base?.bgp;
  const problems = Boolean(routers?.not_running.length || bgp?.not_established.length || failedInstances.length || base?.status === 'unknown');

  return (
    <div className="gs-page gs-dark">
      <div className="gs-row" style={{ marginBottom: 10 }}>
        <div>
          <h2 className="gs-title">{deployment.name !== id ? deployment.name : id}</h2>
          <div className="gs-subtitle" style={{ margin: 0 }}><span className="gs-mono">{id}</span></div>
        </div>
        <span className="gs-spacer" />
        <Link className="gs-btn ghost sm" to={`/d/${id}/map`}>Open map</Link>
        {playerUrl && <a className="gs-btn ghost sm" href={playerUrl} target="_blank" rel="noreferrer">Open in Player ↗</a>}
        {canIn(deployment, 'build') && (
          <div style={{ position: 'relative' }}>
            <button className="gs-btn ghost sm" aria-label="More actions" aria-expanded={menu} onClick={() => setMenu(!menu)}>⋯</button>
            {menu && (
              <div className="gs-menu right" style={{ minWidth: 180 }} role="menu">
                <button role="menuitem" onClick={() => { setMenu(false); configure(); }}>Re-run configure</button>
              </div>
            )}
          </div>
        )}
      </div>
      {error && <div className="gs-banner bad">{error}</div>}
      <div className="gs-box">
        <h3>
          Base {base?.version?.name ? <span className="gs-mono">{base.version.name}</span> : null}
          {base?.version?.scope && <span className="gs-muted" style={{ fontWeight: 400 }}>{base.version.scope}</span>}
          {base && <span className={`gs-pill ${BASE_TONE[base.status]}`}>{base.status.replace('_', ' ')}</span>}
          <span className="gs-spacer" />
          {base && base.status !== 'not_deployed' && (
            <button className="gs-btn ghost sm" aria-expanded={details} onClick={() => setDetails(!details)}>{details ? 'Hide details' : 'Details'}</button>
          )}
        </h3>
        {routers && (
          <div className="gs-muted">
            {count(routers.running, routers.expected, 'routers running')} · {count(bgp?.established, bgp?.total, 'BGP sessions up')}
            {' · '}{instances.length} enclave instance{instances.length === 1 ? '' : 's'}
          </div>
        )}
        {problems && (
          <div style={{ marginTop: 6 }}>
            {base && base.status === 'unknown' && <div className="gs-finding">Health is unknown: Proxmox or Prometheus didn't answer.</div>}
            {routers?.not_running.slice(0, 8).map((r) => <div key={r} className="gs-finding bad">{r} isn't running</div>)}
            {routers && routers.not_running.length > 8 && <div className="gs-muted">and {routers.not_running.length - 8} more routers</div>}
            {bgp?.not_established.slice(0, 8).map((p) => <div key={`${p.router}-${p.peer_address}`} className="gs-finding">{p.router}: BGP to {p.peer_address} is {p.state}</div>)}
            {bgp && bgp.not_established.length > 8 && <div className="gs-muted">and {bgp.not_established.length - 8} more sessions</div>}
            {failedInstances.map((i) => <div key={i.id} className="gs-finding bad">Instance {i.id} failed{i.error ? `: ${i.error}` : ''}</div>)}
          </div>
        )}
        {details && base && <BaseDetails base={base} />}
      </div>
      <div className="gs-grid2" style={{ alignItems: 'start' }}>
        <div>
          <div className="gs-box">
            <h3>Enclave instances <span className="gs-spacer" /><Link className="gs-btn ghost sm" to={`/d/${id}/enclaves`}>All</Link></h3>
            {instances.length === 0 ? <div className="gs-muted">None.</div> : (
              <table className="gs-table">
                <thead><tr><th>Instance</th><th>Enclave</th><th>State</th><th>Hosts</th></tr></thead>
                <tbody>{instances.map((i) => (
                  <tr key={i.id}>
                    <td className="gs-mono">{i.id}</td><td>{i.slice.slug}</td>
                    <td><span className={`gs-pill ${INSTANCE_TONE[i.state] ?? ''}`}>{i.state}</span></td><td>{i.hosts}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </div>
        <div>
          <div className="gs-box">
            <h3>Recent jobs <span className="gs-spacer" /><Link className="gs-btn ghost sm" to={`/d/${id}/jobs`}>Jobs</Link></h3>
            {jobs.length === 0 ? <div className="gs-muted">No jobs yet.</div> : (
              <table className="gs-table gs-clickable">
                <tbody>{jobs.map((j) => (
                  <tr key={j.id} onClick={() => navigate(`/d/${id}/jobs/${j.id}`)}>
                    <td>{j.parent ? '↳ ' : ''}{kindLabel(j.kind)}</td><td><span className="gs-tag">{j.target}</span></td>
                    <td><StatePill state={j.state} /></td><td className="gs-muted">{ago(j.created)}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>
          {(canIn(deployment, 'build') || canIn(deployment, 'delete')) && (
            <div className="gs-box">
              <h3>Lifecycle</h3>
              <div className="gs-row" style={{ flexWrap: 'wrap' }}>
                {canIn(deployment, 'build') && <Link className="gs-btn ghost sm" to={`/d/${id}/build`}>Change configuration</Link>}
                <span className="gs-spacer" />
                {canIn(deployment, 'delete') && <button className="gs-btn bad sm" onClick={() => setDestroying(true)}>Destroy deployment…</button>}
              </div>
            </div>
          )}
        </div>
      </div>
      {destroying && (
        <DestroyModal deployment={deployment} instances={instances.length} onClose={() => setDestroying(false)}
          onDone={(jobId) => { setDestroying(false); navigate(`/d/${id}/jobs/${jobId}`); }} />
      )}
    </div>
  );
};
