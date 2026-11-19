/**
 * Admin > Platform: the Proxmox clusters and Crucible instances deployments run on (GET
 * /api/v1/platform). Defined once here; new deployments choose a connection and a node, and
 * whether they get a Player view, instead of typing settings and credentials.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { control } from '../../api/control';
import { ConnectionTest, CrucibleConnection, NodeInfo, Platform, ProxmoxConnection } from '../../types/control';
import { CrucibleConnectionForm, ProxmoxConnectionForm } from './ConnectionForms';
import { fullness, NodeModal, nodeOf, summary, tone } from './NodeCapacity';

const TONE: Record<string, string> = { ok: 'ok', warning: 'warn', error: 'bad' };

const Checks: React.FC<{ test: ConnectionTest }> = ({ test }) => (
  <div style={{ marginTop: 8 }}>
    {test.checks.map((c) => (
      <div key={`${c.name}-${c.message}`} className="gs-row" style={{ alignItems: 'flex-start', padding: '2px 0' }}>
        <span className={`gs-dot ${TONE[c.status]}`} style={{ marginTop: 4 }} />
        <span><span className="gs-mono gs-muted">{c.name}</span> {c.message}</span>
      </div>
    ))}
  </div>
);

const Uses: React.FC<{ ids: string[] }> = ({ ids }) => (
  <div className="gs-row" style={{ flexWrap: 'wrap', gap: 5, marginTop: 6 }}>
    <span className="gs-muted">Deployments</span>
    {ids.length ? ids.map((d) => <Link key={d} className="gs-tag" to={`/d/${d}/settings`}>{d}</Link>) : <span className="gs-muted">none</span>}
  </div>
);

export const PlatformPage: React.FC = () => {
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [tests, setTests] = useState<Record<string, ConnectionTest | 'testing'>>({});
  const [editing, setEditing] = useState<{ kind: 'proxmox'; c?: ProxmoxConnection } | { kind: 'crucible'; c?: CrucibleConnection } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [opened, setOpened] = useState<{ c: ProxmoxConnection; name: string } | null>(null);
  useEffect(() => {
    const loadNodes = () => control.nodes().then(setNodes).catch(() => undefined);
    loadNodes();
    const timer = setInterval(loadNodes, 15000);
    return () => clearInterval(timer);
  }, []);

  const load = useCallback(() => control.platform().then(setPlatform).catch((err) => setError(err.message)), []);
  useEffect(() => {
    load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setError(null);
    setMessage(null);
    try {
      await fn();
      setMessage(done);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const test = async (key: string, fn: () => Promise<ConnectionTest>) => {
    setTests((t) => ({ ...t, [key]: 'testing' }));
    try {
      const result = await fn();
      setTests((t) => ({ ...t, [key]: result }));
    } catch (err) {
      setTests((t) => ({ ...t, [key]: { ok: false, checks: [{ name: 'test', status: 'error', message: err instanceof Error ? err.message : String(err) }] } }));
    }
  };
  const status = (key: string) => {
    const t = tests[key];
    if (!t) return null;
    if (t === 'testing') return <span className="gs-pill">testing…</span>;
    return <span className={`gs-pill ${t.ok ? 'ok' : 'bad'}`}>{t.ok ? 'works' : 'failed'}</span>;
  };
  const saved = (text: string) => {
    setEditing(null);
    setMessage(text);
    load();
  };

  if (!platform) return error ? <div className="gs-banner bad">{error}</div> : <div className="gs-empty">Loading the platform…</div>;
  return (
    <div>
      {message && <div className="gs-banner info">{message}</div>}
      {error && <div className="gs-banner bad">{error}</div>}
      <div className="gs-grid2" style={{ alignItems: 'start' }}>
        <div className="gs-box">
          <h3>Proxmox <span className="gs-spacer" /><button className="gs-btn ghost sm" onClick={() => setEditing({ kind: 'proxmox' })}>＋ Connection</button></h3>
          {platform.proxmox.length === 0 && <div className="gs-muted">No Proxmox connections: deployments can't be created until there is one.</div>}
          {platform.proxmox.map((c) => (
            <div key={c.id} className="gs-box" style={{ background: 'transparent' }}>
              <div className="gs-row">
                <b>{c.name}</b> <span className="gs-mono gs-muted">{c.id}</span>
                {platform.default_proxmox === c.id && <span className="gs-pill info">default</span>}
                {status(`proxmox:${c.id}`)}
                <span className="gs-spacer" />
                <button className="gs-btn ghost sm" onClick={() => test(`proxmox:${c.id}`, () => control.testProxmox(c.id))}>Test</button>
                <button className="gs-btn ghost sm" onClick={() => setEditing({ kind: 'proxmox', c })}>Edit</button>
                {platform.default_proxmox !== c.id && <button className="gs-btn ghost sm" onClick={() => act(() => control.setDefaultProxmox(c.id), `${c.id} is the default`)}>Make default</button>}
                <button className="gs-btn ghost sm" disabled={c.deployments.length > 0} title={c.deployments.length ? 'Deployments run on it' : undefined}
                  onClick={() => window.confirm(`Delete the connection ${c.id}?`) && act(() => control.deleteProxmox(c.id), `Deleted ${c.id}`)}>Delete</button>
              </div>
              <div className="gs-muted" style={{ marginTop: 4 }}>
                <span className="gs-mono">{c.host}:{c.port}</span> · {c.auth === 'token' ? 'API token' : `password (${c.username || 'no user'})`}
                {(c.apply_parallelism || c.plan_parallelism) ? <> · {[c.apply_parallelism && `${c.apply_parallelism} created at once`,
                  c.plan_parallelism && `${c.plan_parallelism} parallel reads`].filter(Boolean).join(', ')}</> : null}
                {c.proxy_mode === 'none' ? ' · no proxy' : c.proxy_mode === 'custom' ? <> · proxy <span className="gs-mono">{c.proxy}</span></> : null}
                {(c.auth === 'token' ? !c.secrets.includes('api_token') : !c.secrets.includes('password')) && <span className="gs-pill warn" style={{ marginLeft: 6 }}>no credentials</span>}
              </div>
              <div className="gs-row" style={{ flexWrap: 'wrap', gap: 5, marginTop: 6 }}>
                <span className="gs-muted">Nodes</span>
                {c.nodes.map((n) => {
                  const info = nodeOf(nodes, c, n.name);
                  const share = fullness(info);
                  return (
                    <button key={n.name} className={`gs-tag gs-node ${tone(share)}`} onClick={() => setOpened({ c, name: n.name })}
                      title={`${summary(info)}\n${[n.blockstore, n.filestore, n.mgmt_bridge, n.vm_bridge].filter(Boolean).join(' · ') || 'the default storage and bridges'}`}>
                      {n.name}{n.name === c.default_node ? ' ★' : ''}
                    </button>
                  );
                })}
              </div>
              <Uses ids={c.deployments} />
              {tests[`proxmox:${c.id}`] && tests[`proxmox:${c.id}`] !== 'testing' && <Checks test={tests[`proxmox:${c.id}`] as ConnectionTest} />}
            </div>
          ))}
        </div>

        <div className="gs-box">
          <h3>Crucible <span className="gs-spacer" /><button className="gs-btn ghost sm" onClick={() => setEditing({ kind: 'crucible' })}>＋ Connection</button></h3>
          {platform.crucible.length === 0 && <div className="gs-muted">No Crucible connection: deployments can't have a Player view until there is one.</div>}
          {platform.crucible.map((c) => (
            <div key={c.id} className="gs-box" style={{ background: 'transparent' }}>
              <div className="gs-row">
                <b>{c.name}</b> <span className="gs-mono gs-muted">{c.id}</span>
                {status(`crucible:${c.id}`)}
                <span className="gs-spacer" />
                <button className="gs-btn ghost sm" onClick={() => test(`crucible:${c.id}`, () => control.testCrucible(c.id))}>Test</button>
                <button className="gs-btn ghost sm" onClick={() => setEditing({ kind: 'crucible', c })}>Edit</button>
                <button className="gs-btn ghost sm" disabled={c.deployments.length > 0} title={c.deployments.length ? 'Deployments have a Player view through it' : undefined}
                  onClick={() => window.confirm(`Delete the connection ${c.id}?`) && act(() => control.deleteCrucible(c.id), `Deleted ${c.id}`)}>Delete</button>
              </div>
              <div className="gs-muted" style={{ marginTop: 4 }}>
                <span className="gs-mono">{c.player_api_url}</span> · {c.username || 'no account'}
                {!c.secrets.includes('password') && <span className="gs-pill warn" style={{ marginLeft: 6 }}>no password</span>}
              </div>
              <Uses ids={c.deployments} />
              {tests[`crucible:${c.id}`] && tests[`crucible:${c.id}`] !== 'testing' && <Checks test={tests[`crucible:${c.id}`] as ConnectionTest} />}
            </div>
          ))}
        </div>
      </div>
      {editing?.kind === 'proxmox' && <ProxmoxConnectionForm existing={editing.c} onClose={() => setEditing(null)} onSaved={saved} />}
      {editing?.kind === 'crucible' && <CrucibleConnectionForm existing={editing.c} onClose={() => setEditing(null)} onSaved={saved} />}
      {opened && <NodeModal name={opened.name} connection={opened.c.name} n={nodeOf(nodes, opened.c, opened.name)} onClose={() => setOpened(null)} />}
    </div>
  );
};
