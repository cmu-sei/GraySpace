/**
 * The deployment's enclave instances. An instance opens on its sites and their hosts (cards with
 * Desktop, Console, Stop); Manage has its reset, DNS records, certificates, jobs and
 * Destroy. Which enclaves are built is the builder's (Configuration); here they are used.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { canIn } from '../../auth/AuthProvider';
import { Tip } from '../../components/Tip';
import { TERRAIN_EVENT } from '../../hooks/useTerrainEvents';
import { apiService } from '../../services/api';
import { Deployment } from '../../types/control';
import { ActionRequest, InstanceSummary, InstanceView, JobRecord, ManifestHost, ManifestSite } from '../../types/slices';
import { ago, kindLabel } from '../jobs/format';
import { JobDetail, StatePill } from '../jobs/JobDetail';

const ACTIVE = ['pending', 'provisioning', 'configuring', 'destroying'];
const TONE: Record<string, string> = { ready: 'ok', failed: 'bad', destroying: 'warn', destroyed: '', pending: '', provisioning: 'info', configuring: 'info' };

const enclaveOf = (i: { enclave?: { slug: string; version: string }; slice: { slug: string; version: string } }) => i.enclave ?? i.slice;

export const InstanceState: React.FC<{ state: string }> = ({ state }) => (
  <span className={`gs-pill ${TONE[state] ?? ''}`}>{ACTIVE.includes(state) ? '⟳ ' : ''}{state}</span>
);

type Panel = 'hosts' | 'manage';

/** Hosts under their sites, in the spec's order; hosts on no site (internet hosts) last */
const hostGroups = (sites: ManifestSite[], hosts: ManifestHost[]) => {
  const groups: { site: ManifestSite | null; hosts: ManifestHost[] }[] = sites.map((site) => ({ site, hosts: hosts.filter((h) => h.site === site.name) }));
  const elsewhere = hosts.filter((h) => !sites.some((s) => s.name === h.site));
  if (elsewhere.length) groups.push({ site: null, hosts: elsewhere });
  return groups;
};

const HOST_TONE: Record<string, string> = { running: 'ok', stopped: 'warn' };

const HostCard: React.FC<{
  host: ManifestHost; multiSegment: boolean; canOperate: boolean; ready: boolean; busy: boolean; desktop: boolean;
  onDesktop: (hostname: string) => void; onConsole: (hostname: string) => void; onPower: (action: 'host.stop' | 'host.start') => void;
}> = ({ host: h, multiSegment, canOperate, ready, busy, desktop, onDesktop, onConsole, onPower }) => {
  const running = h.status === 'running';
  return (
    <div className={`dns-card gs-host-card ${HOST_TONE[h.status] ?? ''}`}>
      <div className="dns-card-header">
        <span className="dns-card-hostname">{h.hostname}</span>
        <span className={`gs-pill ${HOST_TONE[h.status] ?? ''}`}>{h.status}</span>
      </div>
      <div className="dns-card-body">
        <p><strong>IP:</strong> <span className="gs-mono">{h.ip}</span>{h.public_ip && <> · <span className="gs-mono">{h.public_ip}</span> public</>}</p>
        {multiSegment && h.segment && <p><strong>Segment:</strong> {h.segment}</p>}
        <p><strong>Image:</strong> {h.image}</p>
        {h.mgmt_ip && <p><strong>Management:</strong> <span className="gs-mono">{h.mgmt_ip}</span></p>}
        {(h.fqdns.length > 0 || h.roles.length > 0) && (
          <div className="dns-card-zones">
            {h.roles.map((r) => <span key={r} className="gs-tag">{r}</span>)}
            {h.fqdns.map((f) => <span key={f} className="zone-tag">{f}</span>)}
          </div>
        )}
      </div>
      {canOperate && (
        <div className="gs-host-card-actions">
          {h.mgmt_ip && running && desktop && <button className="gs-btn sm" onClick={() => onDesktop(h.hostname)}>Desktop</button>}
          {h.mgmt_ip && running && <button className="gs-btn ghost sm" onClick={() => onConsole(h.hostname)}>Console</button>}
          <span className="gs-spacer" />
          {ready && running && <button className="gs-btn ghost sm" disabled={busy} onClick={() => onPower('host.stop')}>Stop</button>}
          {ready && h.status === 'stopped' && <button className="gs-btn ghost sm" disabled={busy} onClick={() => onPower('host.start')}>Start</button>}
        </div>
      )}
    </div>
  );
};

interface Props {
  deployment: Deployment;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onShowOnMap: (lat: number, lng: number) => void;
  onOpenConsole: (hostname: string) => void;
  onOpenDesktop: (hostname: string) => void;
}

export const EnclavesPage: React.FC<Props> = ({ deployment, selectedId, onSelect, onShowOnMap, onOpenConsole, onOpenDesktop }) => {
  const [instances, setInstances] = useState<InstanceSummary[]>([]);
  const [showDestroyed, setShowDestroyed] = useState(false);
  const [view, setView] = useState<InstanceView | null>(null);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Images with a desktop in the browser (the `desktop` feature, e.g. grayspace-desktop)
  const [desktopImages, setDesktopImages] = useState<Set<string>>(new Set());
  // Hosts (sites and their hosts) or Manage (actions, records, jobs, Destroy); null follows the state
  const [panel, setPanel] = useState<Panel | null>(null);
  const canBuild = canIn(deployment, 'build');
  const canOperate = canIn(deployment, 'operate');

  const loadList = useCallback(async () => {
    try {
      setInstances(await apiService.getInstances(showDestroyed));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [showDestroyed]);

  const loadSelected = useCallback(async () => {
    if (!selectedId) return;
    try {
      const [v, j] = await Promise.all([apiService.getInstance(selectedId), apiService.getJobs(selectedId)]);
      setView(v);
      setJobs(j);
      setJobId((current) => current ?? j[0]?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [selectedId]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    apiService.getImages()
      .then((images) => setDesktopImages(new Set(images.filter((i) => i.features.includes('desktop')).map((i) => i.name))))
      .catch(() => undefined);
  }, []);

  // Open the first instance when none is chosen
  useEffect(() => {
    if (!selectedId && instances.length) onSelect(instances[0].id);
  }, [selectedId, instances, onSelect]);

  useEffect(() => {
    setView(null);
    setJobs([]);
    setJobId(null);
    setError(null);
    setMessage(null);
    setPanel(null);
    loadSelected();
  }, [selectedId, loadSelected]);

  // Refresh on instance and job events, and every 10 s while something is in progress
  useEffect(() => {
    const onEvent = (e: Event) => {
      const type = (e as CustomEvent).detail?.type;
      if (type === 'instance.update' || type === 'job.update') {
        loadList();
        loadSelected();
      }
    };
    window.addEventListener(TERRAIN_EVENT, onEvent);
    const active = instances.some((i) => ACTIVE.includes(i.state));
    const timer = active ? setInterval(() => { loadList(); loadSelected(); }, 10000) : undefined;
    return () => {
      window.removeEventListener(TERRAIN_EVENT, onEvent);
      if (timer) clearInterval(timer);
    };
  }, [instances, loadList, loadSelected]);

  const act = async (request: ActionRequest, label: string) => {
    if (!view) return;
    setBusy(label);
    setError(null);
    setMessage(null);
    try {
      setMessage((await apiService.instanceAction(view.id, request)).message);
      await loadSelected();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const destroy = async () => {
    if (!view || !window.confirm(`Destroy instance ${view.id}? Its hosts, DNS records and certificates are removed.`)) return;
    try {
      setJobId((await apiService.destroyInstance(view.id)).job.id);
      setPanel('manage');
      await Promise.all([loadSelected(), loadList()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const showSite = (site: string) => view && apiService.getMap(view.id).then((fc) => {
    const f = fc.features.find((x) => x.id === `${view.id}/${site}`);
    if (f && f.geometry.type === 'Point') onShowOnMap(f.geometry.coordinates[1], f.geometry.coordinates[0]);
  }).catch(() => undefined);

  const ready = view?.state === 'ready';
  // A built instance opens on its hosts; one being built, failed or destroyed on its jobs
  const tab: Panel = panel ?? (view && !view.hosts.length && view.state !== 'ready' ? 'manage' : 'hosts');
  const groups = view ? hostGroups(view.sites, view.hosts) : [];

  return (
    <div className="gs-page gs-dark">
      <div className="gs-row" style={{ marginBottom: 10 }}>
        <div>
          <h2 className="gs-title">Enclaves</h2>
        </div>
        <span className="gs-spacer" />
        <label className="gs-row gs-muted"><input type="checkbox" checked={showDestroyed} onChange={(e) => setShowDestroyed(e.target.checked)} /> show destroyed</label>
      </div>
      {error && <div className="gs-banner bad">{error}</div>}
      <div className="gs-split" style={{ gridTemplateColumns: 'minmax(320px, 0.8fr) minmax(0, 2fr)' }}>
        <div className="gs-box" style={{ padding: 0 }}>
          {instances.length === 0 ? (
            <div className="gs-empty">No enclaves deployed.{canBuild && <> <Link to={`/d/${deployment.id}/build/enclaves`}>Add enclaves</Link></>}</div>
          ) : (
            <table className="gs-table gs-clickable">
              <thead><tr><th>Instance</th><th>Enclave</th><th>State</th><th>Hosts</th></tr></thead>
              <tbody>
                {instances.map((i) => (
                  <tr key={i.id} className={selectedId === i.id ? 'selected' : ''} onClick={() => onSelect(i.id)}>
                    <td className="gs-mono">{i.id}</td>
                    <td>{enclaveOf(i).slug}<div className="gs-muted" style={{ fontSize: 10.5 }}>{i.sites} site{i.sites === 1 ? '' : 's'}</div></td>
                    <td><InstanceState state={i.state} />{i.error && <div className="gs-muted" style={{ fontSize: 10.5, color: 'var(--gs-bad)' }}>{i.error.slice(0, 80)}</div>}</td>
                    <td>{i.hosts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div>
          {view && (
            <>
              <div className="gs-box">
                <h3 style={{ margin: 0 }}>
                  <span className="gs-mono">{view.id}</span> <InstanceState state={view.state} />
                  <span className="gs-muted" style={{ fontWeight: 400 }}>
                    {enclaveOf(view).slug}{view.base && <> · {view.base.name ?? 'local CSVs'}</>} · {ago(view.created)}
                  </span>
                  <Tip text={`Version ${enclaveOf(view).version.replace('sha256:', '').slice(0, 12)}${view.base ? `, ${view.base.scope} core` : ''}. Created ${new Date(view.created).toLocaleString()}.`} />
                  <span className="gs-spacer" />
                  <div className="gs-seg" role="group" aria-label="Instance view">
                    <button className={tab === 'hosts' ? 'on' : ''} aria-pressed={tab === 'hosts'} onClick={() => setPanel('hosts')}>Hosts</button>
                    <button className={tab === 'manage' ? 'on' : ''} aria-pressed={tab === 'manage'} onClick={() => setPanel('manage')}>⚙ Manage</button>
                  </div>
                </h3>
                {view.error && <div className="gs-banner bad" style={{ marginTop: 8, marginBottom: 0 }}>{view.error}</div>}
                {message && <div className="gs-banner info" style={{ marginTop: 8, marginBottom: 0 }}>{message}</div>}
              </div>

              {tab === 'hosts' ? (
                <>
                  {groups.map(({ site, hosts }) => (
                    <div className="gs-box" key={site?.name ?? '(internet)'}>
                      <h3>
                        {site ? site.name : 'Internet'}
                        {site && <span className="gs-muted gs-mono" style={{ fontWeight: 400, fontSize: 11 }}>
                          {site.wans.map((w) => w.ip).join(', ')} · {site.segments.map((g) => `${g.name} ${g.cidr}`).join(', ')}
                        </span>}
                        {site && <Tip text={`Edge ${site.edge}, attached to ${site.wans.map((w) => `${w.router} (${w.isp}${w.location ? `, ${w.location}` : ''})`).join(', ')}${site.routed ? `. Routed ${site.public_prefix}` : ''}`} />}
                        <span className="gs-spacer" />
                        {site && <button className="gs-btn ghost sm" onClick={() => showSite(site.name)}>Map</button>}
                      </h3>
                      {hosts.length === 0 ? <div className="gs-muted">No hosts.</div> : (
                        <div className="dns-cards-grid">
                          {hosts.map((h) => <HostCard key={h.hostname} host={h} multiSegment={(site?.segments.length ?? 0) > 1}
                            canOperate={canOperate} ready={ready} busy={Boolean(busy)} desktop={desktopImages.has(h.image)}
                            onDesktop={onOpenDesktop} onConsole={onOpenConsole}
                            onPower={(action) => act({ action, target: h.hostname }, h.hostname)} />)}
                        </div>
                      )}
                    </div>
                  ))}
                  {groups.length === 0 && <div className="gs-box"><div className="gs-muted">{ready ? 'No hosts.' : 'Hosts appear here once the instance is built.'}</div></div>}
                </>
              ) : (
                <>
                  {(ready || canBuild) && (
                    <div className="gs-box">
                      <h3>Actions</h3>
                      <div className="gs-row">
                        {canOperate && ready && (
                          <button className="gs-btn ghost sm" disabled={Boolean(busy)} title="Clear added link conditions and DNS records, bring links up, start stopped hosts"
                            onClick={() => act({ action: 'reset' }, 'reset')}>{busy === 'reset' ? 'Resetting…' : 'Reset actions'}</button>
                        )}
                        <span className="gs-spacer" />
                        {canBuild && view.state !== 'destroyed' && view.state !== 'destroying' && (
                          <button className="gs-btn bad sm" title="Remove its hosts, DNS records and certificates" onClick={destroy}>Destroy</button>
                        )}
                      </div>
                    </div>
                  )}

                  {(view.dns.length > 0 || view.certificates.length > 0) && (
                    <div className="gs-grid2" style={{ alignItems: 'start' }}>
                      <div className="gs-box">
                        <h3>DNS records</h3>
                        {view.dns.length === 0 ? <div className="gs-muted">None.</div> : (
                          <table className="gs-table">
                            <thead><tr><th>Name</th><th>Type</th><th>Data</th></tr></thead>
                            <tbody>{view.dns.map((r) => <tr key={`${r.name}-${r.type}-${r.data}`}><td className="gs-mono">{r.name}</td><td>{r.type}</td><td className="gs-mono">{r.data}</td></tr>)}</tbody>
                          </table>
                        )}
                      </div>
                      <div className="gs-box">
                        <h3>Certificates</h3>
                        {view.certificates.length === 0 ? <div className="gs-muted">None.</div> : (
                          <table className="gs-table">
                            <thead><tr><th>Host</th><th>Names</th><th>CA</th></tr></thead>
                            <tbody>{view.certificates.map((c) => <tr key={c.host}><td className="gs-mono">{c.host}</td><td className="gs-mono" style={{ fontSize: 11 }}>{c.names.join(', ')}</td><td>{c.ca}</td></tr>)}</tbody>
                          </table>
                        )}
                      </div>
                    </div>
                  )}

                  <div className="gs-box">
                    <h3>Jobs</h3>
                    <table className="gs-table gs-clickable">
                      <tbody>{jobs.map((j) => (
                        <tr key={j.id} className={jobId === j.id ? 'selected' : ''} onClick={() => setJobId(j.id)}>
                          <td>{kindLabel(j.kind)}{j.retry_of && <span className="gs-muted"> (retry)</span>}</td>
                          <td><StatePill state={j.state} /></td>
                          <td className="gs-muted">{j.stage ?? ''}{j.attempt ? ` (${j.attempt})` : ''}</td>
                          <td className="gs-muted">{ago(j.created)}</td>
                          <td style={{ textAlign: 'right' }}><Link className="gs-btn ghost sm" to={`/d/${deployment.id}/jobs/${j.id}`} onClick={(e) => e.stopPropagation()}>Open in Jobs</Link></td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                  {jobId && <JobDetail key={jobId} deploymentId={deployment.id} jobId={jobId} canBuild={canBuild} onRetried={(id) => { setJobId(id); loadSelected(); }} />}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
