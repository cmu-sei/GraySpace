/**
 * Adding and changing a Proxmox or Crucible connection (Admin > Platform). Secrets are write-only:
 * the form says whether one is set, sends a new one when typed, and clears one only when asked.
 */
import React, { useState } from 'react';
import { control } from '../../api/control';
import { CrucibleConnection, CrucibleConnectionWrite, Discovery, ProxmoxConnection, ProxmoxConnectionWrite, ProxmoxNode } from '../../types/control';

const ID = /^[a-z][a-z0-9-]{0,31}$/;

/** A write-only field: empty keeps what is set, unless cleared. */
const Secret: React.FC<{ id: string; label: string; set: boolean; value: string | undefined; onChange: (v: string | undefined) => void }> = ({ id, label, set, value, onChange }) => (
  <div className="gs-field">
    <label htmlFor={id}>{label}</label>
    <div className="gs-row">
      <input id={id} type="password" autoComplete="new-password" value={value ?? ''} placeholder={value === '' ? 'will be cleared' : set ? 'set; type to replace' : 'not set'}
        onChange={(e) => onChange(e.target.value || undefined)} />
      {set && value === undefined && <button type="button" className="gs-btn ghost sm" onClick={() => onChange('')}>Clear</button>}
    </div>
  </div>
);

const NODE_FIELDS: [keyof ProxmoxNode, string][] = [['blockstore', 'Block storage'], ['filestore', 'File storage'], ['mgmt_bridge', 'Management bridge'], ['vm_bridge', 'VM bridge']];

export const ProxmoxConnectionForm: React.FC<{ existing?: ProxmoxConnection; onClose: () => void; onSaved: (message: string) => void }> = ({ existing, onClose, onSaved }) => {
  const [id, setId] = useState(existing?.id ?? '');
  const [form, setForm] = useState<ProxmoxConnectionWrite>(existing ? {
    name: existing.name, host: existing.host, port: existing.port, verify_ssl: existing.verify_ssl, auth: existing.auth,
    username: existing.username ?? '', ssh_username: existing.ssh_username ?? '', default_node: existing.default_node,
    proxy_mode: existing.proxy_mode ?? 'site', proxy: existing.proxy ?? '', no_proxy: existing.no_proxy ?? '',
  } : { port: 8006, auth: 'token', ssh_username: 'root', proxy_mode: 'site' });
  const [nodes, setNodes] = useState<ProxmoxNode[]>(existing?.nodes ?? [{ name: '' }]);
  // Empty: the server's default
  const [applyParallelism, setApplyParallelism] = useState(existing?.apply_parallelism ? String(existing.apply_parallelism) : '');
  const [planParallelism, setPlanParallelism] = useState(existing?.plan_parallelism ? String(existing.plan_parallelism) : '');
  const [discovery, setDiscovery] = useState<Discovery | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const secretSet = (name: string) => Boolean(existing?.secrets.includes(name));
  const set = (patch: ProxmoxConnectionWrite) => setForm({ ...form, ...patch });
  const setNode = (i: number, patch: Partial<ProxmoxNode>) => setNodes(nodes.map((n, j) => (j === i ? { ...n, ...patch } : n)));
  const parallelismValid = (v: string) => v === '' || (/^\d+$/.test(v) && Number(v) >= 1 && Number(v) <= 100);
  const proxyValid = form.proxy_mode !== 'custom' || /^https?:\/\/\S+$/.test(form.proxy ?? '');
  const valid = (existing || ID.test(id)) && form.host && nodes.length > 0 && nodes.every((n) => n.name)
    && parallelismValid(applyParallelism) && parallelismValid(planParallelism) && proxyValid;
  // A value: that; emptied: 0, which clears a saved one back to the server's default; never set: left out
  const parallelism = (v: string, saved?: number | null) => (v !== '' ? Number(v) : saved ? 0 : undefined);

  const discover = async () => {
    if (!existing) return;
    setBusy(true);
    setError(null);
    try {
      const found = await control.discoverProxmox(existing.id);
      setDiscovery(found);
      if (nodes.length === 1 && !nodes[0].name) setNodes(found.nodes.filter((n) => n.status === 'online').map((n) => ({ name: n.name })));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body: ProxmoxConnectionWrite = {
      ...form, nodes: nodes.map((n) => ({ ...n })),
      apply_parallelism: parallelism(applyParallelism, existing?.apply_parallelism),
      plan_parallelism: parallelism(planParallelism, existing?.plan_parallelism),
    };
    try {
      if (existing) {
        const r = await control.updateProxmox(existing.id, body);
        onSaved(r.restart_pending.length ? `Saved; ${r.restart_pending.join(', ')} restart once their jobs end` : 'Saved');
      } else {
        await control.createProxmox({ ...body, id });
        onSaved(`Added ${id}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  const choices = (name: string, key: keyof ProxmoxNode): string[] => {
    const found = discovery?.nodes.find((n) => n.name === name);
    if (!found) return [];
    // A guest's bridge can be the host's own or an SDN vnet
    if (key === 'mgmt_bridge' || key === 'vm_bridge') return [...found.bridges, ...(discovery?.vnets ?? []).filter((v) => !found.bridges.includes(v))];
    const wanted = key === 'blockstore' ? 'images' : 'vztmpl';
    return found.storages.filter((s) => !s.content || s.content.includes(wanted) || (key === 'filestore' && s.content.includes('snippets'))).map((s) => s.storage);
  };

  return (
    <div className="gs-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="gs-modal" style={{ width: 760 }} onSubmit={save} aria-label="Proxmox connection">
        <h3>{existing ? `Proxmox connection ${existing.id}` : 'New Proxmox connection'}</h3>
        <div className="gs-grid2">
          <div className="gs-field">
            <label htmlFor="px-id">ID</label>
            <input id="px-id" className="gs-mono" value={id} disabled={Boolean(existing)} onChange={(e) => setId(e.target.value.toLowerCase())} placeholder="lab" />
            {!existing && id && !ID.test(id) && <div className="gs-hint">Lowercase letters, digits and hyphens, starting with a letter</div>}
          </div>
          <div className="gs-field"><label htmlFor="px-name">Name</label><input id="px-name" value={form.name ?? ''} onChange={(e) => set({ name: e.target.value })} placeholder="Lab cluster" /></div>
          <div className="gs-field"><label htmlFor="px-host">API host</label><input id="px-host" value={form.host ?? ''} onChange={(e) => set({ host: e.target.value })} placeholder="pve1.example.org" /></div>
          <div className="gs-field"><label htmlFor="px-port">Port</label><input id="px-port" type="number" value={form.port ?? 8006} onChange={(e) => set({ port: Number(e.target.value) || 8006 })} /></div>
        </div>
        <div className="gs-row" style={{ gap: 16, margin: '4px 0 8px' }}>
          <div className="gs-seg" role="radiogroup" aria-label="Authentication">
            <button type="button" className={form.auth === 'token' ? 'on' : ''} onClick={() => set({ auth: 'token' })}>API token</button>
            <button type="button" className={form.auth === 'password' ? 'on' : ''} onClick={() => set({ auth: 'password' })}>Username and password</button>
          </div>
          <label className="gs-row"><input type="checkbox" checked={Boolean(form.verify_ssl)} onChange={(e) => set({ verify_ssl: e.target.checked })} /> Check its certificate</label>
        </div>
        <div className="gs-row" style={{ gap: 16, margin: '4px 0 8px' }}>
          <span>Guests' proxy</span>
          <div className="gs-seg" role="radiogroup" aria-label="Guests' proxy">
            <button type="button" className={form.proxy_mode === 'site' ? 'on' : ''} onClick={() => set({ proxy_mode: 'site' })}>The site's</button>
            <button type="button" className={form.proxy_mode === 'none' ? 'on' : ''} onClick={() => set({ proxy_mode: 'none' })}>None</button>
            <button type="button" className={form.proxy_mode === 'custom' ? 'on' : ''} onClick={() => set({ proxy_mode: 'custom' })}>This one</button>
          </div>
        </div>
        <div className="gs-hint" style={{ marginBottom: 8 }}>
          {form.proxy_mode === 'site' ? "What guests install packages through: the site file's TF_VAR_proxy, or none if it has none"
            : form.proxy_mode === 'none' ? 'Guests reach the internet directly over management, even where the site has a proxy'
              : 'Guests install packages through this proxy'}
        </div>
        {form.proxy_mode === 'custom' && <div className="gs-grid2">
          <div className="gs-field">
            <label htmlFor="px-proxy">Proxy</label>
            <input id="px-proxy" className="gs-mono" value={form.proxy ?? ''} onChange={(e) => set({ proxy: e.target.value })} placeholder="http://proxy.example.org:3128" />
            {!proxyValid && <div className="gs-hint">An http:// or https:// URL</div>}
          </div>
          <div className="gs-field">
            <label htmlFor="px-no-proxy">Reached without it</label>
            <input id="px-no-proxy" className="gs-mono" value={form.no_proxy ?? ''} onChange={(e) => set({ no_proxy: e.target.value })} placeholder="localhost,10.0.0.0/8,.example.org" />
          </div>
        </div>}
        <div className="gs-grid2">
          {form.auth === 'token' ? (
            <Secret id="px-token" label="API token (user@realm!name=secret)" set={secretSet('api_token')} value={form.api_token} onChange={(v) => set({ api_token: v })} />
          ) : <>
            <div className="gs-field"><label htmlFor="px-user">Username</label><input id="px-user" value={form.username ?? ''} onChange={(e) => set({ username: e.target.value })} placeholder="terraform@pve" /></div>
            <Secret id="px-password" label="Password" set={secretSet('password')} value={form.password} onChange={(v) => set({ password: v })} />
          </>}
          <div className="gs-field"><label htmlFor="px-ssh-user">SSH user</label><input id="px-ssh-user" value={form.ssh_username ?? ''} onChange={(e) => set({ ssh_username: e.target.value })} /></div>
          <Secret id="px-ssh-password" label="SSH password" set={secretSet('ssh_password')} value={form.ssh_password} onChange={(v) => set({ ssh_password: v })} />
          <div className="gs-field">
            <label htmlFor="px-apply-parallelism">Guests created at once</label>
            <input id="px-apply-parallelism" type="number" min={1} max={100} value={applyParallelism} placeholder="10 (the default)"
              onChange={(e) => setApplyParallelism(e.target.value)} />
            <div className="gs-hint">{parallelismValid(applyParallelism) ? "Terraform's parallelism for applies and destroys. Creates share the storage's lock; too many at once time out"
              : 'A whole number from 1 to 100'}</div>
          </div>
          <div className="gs-field">
            <label htmlFor="px-plan-parallelism">Parallel reads in a plan</label>
            <input id="px-plan-parallelism" type="number" min={1} max={100} value={planParallelism} placeholder="30 (the default)"
              onChange={(e) => setPlanParallelism(e.target.value)} />
            <div className="gs-hint">{parallelismValid(planParallelism) ? "Terraform's parallelism for plans, which only read" : 'A whole number from 1 to 100'}</div>
          </div>
        </div>

        <h3 className="gs-row" style={{ marginTop: 10 }}>Nodes deployments can use <span className="gs-spacer" />
          <button type="button" className="gs-btn ghost sm" disabled={!existing || busy} title={existing ? 'Ask Proxmox for its nodes, storage and bridges' : 'Save the connection first'}
            onClick={discover}>{busy && !discovery ? 'Asking…' : 'Discover'}</button>
        </h3>
        <table className="gs-table">
          <thead><tr><th>Node</th>{NODE_FIELDS.map(([, label]) => <th key={label}>{label}</th>)}<th>Default</th><th /></tr></thead>
          <tbody>{nodes.map((n, i) => (
            <tr key={i}>
              <td>
                {discovery ? (
                  <select className="gs-input" aria-label="Node name" value={n.name} onChange={(e) => setNode(i, { name: e.target.value })}>
                    <option value="">…</option>
                    {discovery.nodes.map((d) => <option key={d.name} value={d.name}>{d.name}{d.status !== 'online' ? ` (${d.status})` : ''}</option>)}
                  </select>
                ) : <input className="gs-input gs-mono" aria-label="Node name" value={n.name} onChange={(e) => setNode(i, { name: e.target.value })} placeholder="pve1" />}
              </td>
              {NODE_FIELDS.map(([key, label]) => {
                const options = choices(n.name, key);
                return (
                  <td key={key}>
                    {options.length ? (
                      <select className="gs-input" aria-label={label} value={(n[key] as string) ?? ''} onChange={(e) => setNode(i, { [key]: e.target.value || null })}>
                        <option value="">the default</option>
                        {options.map((o) => <option key={o} value={o}>{o}</option>)}
                      </select>
                    ) : <input className="gs-input gs-mono" aria-label={label} value={(n[key] as string) ?? ''} onChange={(e) => setNode(i, { [key]: e.target.value || null })} />}
                  </td>
                );
              })}
              <td><input type="radio" name="default-node" aria-label={`Default node ${n.name}`} checked={(form.default_node || nodes[0]?.name) === n.name}
                onChange={() => set({ default_node: n.name })} /></td>
              <td><button type="button" className="gs-btn ghost sm" disabled={nodes.length === 1} onClick={() => setNodes(nodes.filter((_, j) => j !== i))}>Remove</button></td>
            </tr>
          ))}</tbody>
        </table>
        <button type="button" className="gs-btn ghost sm" style={{ marginTop: 6 }} onClick={() => setNodes([...nodes, { name: '' }])}>＋ Node</button>
        {discovery && <div className="gs-muted" style={{ fontSize: 11, marginTop: 6 }}>Proxmox VE {discovery.version} · {discovery.nodes.length} nodes · SDN vnets: {discovery.vnets.length}</div>}
        {existing?.deployments.length ? (
          <div className="gs-muted" style={{ fontSize: 11, marginTop: 6 }}>
            Used by {existing.deployments.join(', ')}: new credentials reach them at once; the host, and a used node's storage and bridges, change only while they have nothing deployed.
          </div>
        ) : null}
        {error && <div className="gs-banner bad" style={{ marginTop: 10 }}>{error}</div>}
        <div className="gs-actions">
          <button type="button" className="gs-btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="gs-btn" disabled={busy || !valid}>{existing ? 'Save' : 'Add the connection'}</button>
        </div>
      </form>
    </div>
  );
};

const CRUCIBLE_FIELDS: [keyof CrucibleConnectionWrite, string, string][] = [
  ['token_url', 'Keycloak token URL', 'https://crucible.example/keycloak/realms/crucible/protocol/openid-connect/token'],
  ['auth_url', 'Keycloak authorization URL', 'https://crucible.example/keycloak/realms/crucible/protocol/openid-connect/auth'],
  ['client_id', 'OAuth client ID', 'the Terraform provider client'],
  ['player_api_url', 'Player API', 'https://crucible.example/player/api'],
  ['player_url', 'Player UI (optional)', 'the Player API URL without /api'],
  ['vm_api_url', 'VM API', 'https://crucible.example/vm/api'],
  ['caster_api_url', 'Caster API', 'https://crucible.example/caster/api'],
  ['username', 'Service account', 'its username'],
  ['service_user_id', 'Service user ID', "the Player user made admin of every view"],
];

export const CrucibleConnectionForm: React.FC<{ existing?: CrucibleConnection; onClose: () => void; onSaved: (message: string) => void }> = ({ existing, onClose, onSaved }) => {
  const [id, setId] = useState(existing?.id ?? '');
  const [form, setForm] = useState<CrucibleConnectionWrite>(existing ? { ...existing } : { client_scopes: ['player-api', 'vm-api'] });
  const [scopes, setScopes] = useState((existing?.client_scopes ?? ['player-api', 'vm-api']).join(' '));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = (existing || ID.test(id)) && form.token_url && form.player_api_url;

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { secrets: _s, deployments: _d, id: _i, ...fields } = form as CrucibleConnection & CrucibleConnectionWrite;
    const body: CrucibleConnectionWrite = { ...fields, client_scopes: scopes.split(/[\s,]+/).filter(Boolean) };
    try {
      if (existing) {
        const r = await control.updateCrucible(existing.id, body);
        onSaved(r.restart_pending.length ? `Saved; ${r.restart_pending.join(', ')} restart once their jobs end` : 'Saved');
      } else {
        await control.createCrucible({ ...body, id });
        onSaved(`Added ${id}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="gs-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="gs-modal" style={{ width: 640 }} onSubmit={save} aria-label="Crucible connection">
        <h3>{existing ? `Crucible connection ${existing.id}` : 'New Crucible connection'}</h3>
        <div className="gs-grid2">
          <div className="gs-field">
            <label htmlFor="cr-id">ID</label>
            <input id="cr-id" className="gs-mono" value={id} disabled={Boolean(existing)} onChange={(e) => setId(e.target.value.toLowerCase())} placeholder="crucible" />
          </div>
          <div className="gs-field"><label htmlFor="cr-name">Name</label><input id="cr-name" value={form.name ?? ''} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
          {CRUCIBLE_FIELDS.map(([key, label, hint]) => (
            <div className="gs-field" key={key}>
              <label htmlFor={`cr-${key}`}>{label}</label>
              <input id={`cr-${key}`} value={(form[key] as string) ?? ''} placeholder={hint} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
            </div>
          ))}
          <Secret id="cr-password" label="Service account password" set={Boolean(existing?.secrets.includes('password'))} value={form.password}
            onChange={(v) => setForm({ ...form, password: v })} />
          <div className="gs-field"><label htmlFor="cr-scopes">Scopes</label><input id="cr-scopes" value={scopes} onChange={(e) => setScopes(e.target.value)} /></div>
        </div>
        {error && <div className="gs-banner bad" style={{ marginTop: 10 }}>{error}</div>}
        <div className="gs-actions">
          <button type="button" className="gs-btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="gs-btn" disabled={busy || !valid}>{existing ? 'Save' : 'Add the connection'}</button>
        </div>
      </form>
    </div>
  );
};
