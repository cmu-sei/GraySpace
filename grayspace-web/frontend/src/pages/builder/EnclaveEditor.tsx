/**
 * One enclave spec, edited in a form (sites, hosts and role variables) or as YAML (everything): both
 * edit the same spec. Fields the form doesn't show (conditions, env, size, labels, publish, internet
 * hosts, multiple attachments) are kept as they are, and edited in YAML.
 */
import React, { useEffect, useState } from 'react';
import { parse, stringify } from 'yaml';
import { Capabilities, EnclaveSpec, Host, Site } from '../../types/build';
import { Tip } from '../../components/Tip';
import { RolePicker } from './RolePicker';
import { RoleVariables } from './RoleVariables';

const LABEL = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;

type Segment = Site['segments'][number];

/** A site's segments as chips; a new one is typed as a name, or name=CIDR. */
const Segments: React.FC<{ segments: Segment[]; disabled: boolean; onChange: (s: Segment[]) => void }> = ({ segments, disabled, onChange }) => {
  const [adding, setAdding] = useState<string | null>(null);
  const add = () => {
    const [name, cidr] = (adding || '').split('=').map((x) => x.trim());
    if (name && !segments.some((g) => g.name === name)) onChange([...segments, cidr ? { name, cidr } : { name }]);
    setAdding(null);
  };
  return (
    <div className="gs-row" style={{ flexWrap: 'wrap', gap: 4 }}>
      {segments.map((g) => (
        <span key={g.name} className="gs-tag">
          {g.name}{g.cidr && <span className="gs-muted"> {g.cidr}</span>}
          {!disabled && segments.length > 1 && (
            <button className="gs-x" style={{ fontSize: 12, marginLeft: 2 }} aria-label={`Remove segment ${g.name}`}
              onClick={() => onChange(segments.filter((x) => x.name !== g.name))}>×</button>
          )}
        </span>
      ))}
      {!disabled && (adding === null
        ? <button className="gs-btn ghost sm" onClick={() => setAdding('')}>＋ Segment</button>
        : <input className="gs-mono" style={{ width: 150 }} autoFocus aria-label="New segment" placeholder="name or name=CIDR" value={adding}
            onChange={(e) => setAdding(e.target.value)} onBlur={add}
            onKeyDown={(e) => { if (e.key === 'Enter') add(); if (e.key === 'Escape') setAdding(null); }} />)}
    </div>
  );
};

const Form: React.FC<{ spec: EnclaveSpec; caps: Capabilities | null; readOnly: boolean; onChange: (s: EnclaveSpec) => void }> = ({ spec, caps, readOnly, onChange }) => {
  const sites = spec.sites || [];
  const hosts = spec.hosts || [];
  const segments = sites.flatMap((s) => s.segments.map((g) => g.name));
  const isps = caps?.isps || [];
  const images = caps?.images || [];
  const setSite = (i: number, site: Site) => onChange({ ...spec, sites: sites.map((s, j) => (j === i ? site : s)) });
  const setHost = (i: number, host: Host) => onChange({ ...spec, hosts: hosts.map((h, j) => (j === i ? host : h)) });
  const disabled = readOnly;

  return (
    <div>
      <div>
        <div className="gs-field">
          <label htmlFor="enc-description">Description</label>
          <input id="enc-description" disabled={disabled} value={spec.description || ''} onChange={(e) => onChange({ ...spec, description: e.target.value })} />
        </div>
      </div>
      <div className="gs-muted" style={{ margin: '8px 0 4px' }}>Sites: an edge router connected to the core, and the networks behind it</div>
      <table className="gs-table gs-form-table">
        <thead><tr><th>Site</th><th>ISP</th><th>Location</th><th>Routed <Tip text="Public addresses, peered with the ISP over BGP. Unticked: private addresses behind NAT." /></th><th>Segments <Tip text="The networks behind the edge router. A new one is a name, or name=CIDR." /></th><th /></tr></thead>
        <tbody>
          {sites.map((site, i) => {
            const attach = Array.isArray(site.attach) ? null : site.attach;
            const locations = isps.find((p) => p.name === attach?.isp)?.locations || [];
            return (
              <tr key={i}>
                <td><input aria-label="Site name" disabled={disabled} value={site.name} onChange={(e) => setSite(i, { ...site, name: e.target.value })} /></td>
                {attach ? (
                  <>
                    <td>
                      <select aria-label="ISP" disabled={disabled} value={attach.isp || ''} onChange={(e) => setSite(i, { ...site, attach: { isp: e.target.value, location: undefined } })}>
                        <option value="">Any</option>
                        {isps.map((p) => <option key={p.name}>{p.name}</option>)}
                        {attach.isp && !isps.some((p) => p.name === attach.isp) && <option>{attach.isp}</option>}
                      </select>
                    </td>
                    <td>
                      <select aria-label="Location" disabled={disabled} value={attach.location || ''} onChange={(e) => setSite(i, { ...site, attach: { ...attach, location: e.target.value || undefined } })}>
                        <option value="">Any</option>
                        {locations.map((l) => <option key={l}>{l}</option>)}
                        {attach.location && !locations.includes(attach.location) && <option>{attach.location}</option>}
                      </select>
                    </td>
                  </>
                ) : <td colSpan={2} className="gs-muted">{(site.attach as unknown[]).length} attachments (YAML)</td>}
                <td><input type="checkbox" aria-label="Routed" disabled={disabled} checked={Boolean(site.routed)} onChange={(e) => setSite(i, { ...site, routed: e.target.checked || undefined })} /></td>
                <td><Segments segments={site.segments} disabled={disabled} onChange={(next) => setSite(i, { ...site, segments: next })} /></td>
                <td>{!disabled && <button className="gs-x" title="Remove the site" onClick={() => onChange({ ...spec, sites: sites.filter((_, j) => j !== i) })}>×</button>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!disabled && (
        <button className="gs-btn ghost sm" style={{ marginTop: 6 }}
          onClick={() => onChange({ ...spec, sites: [...sites, { name: `site${sites.length + 1}`, attach: { isp: isps[0]?.name }, segments: [{ name: `lan${sites.length + 1}` }] }] })}>
          ＋ Site
        </button>
      )}
      <div className="gs-muted" style={{ margin: '12px 0 4px' }}>Hosts</div>
      <table className="gs-table gs-form-table">
        <thead><tr><th>Host</th><th>Count</th><th>Segment</th><th>Image</th><th>Roles</th><th>Crucible <Tip text="Shown in the Player view by default" /></th><th /></tr></thead>
        <tbody>
          {hosts.map((host, i) => (
            <tr key={i}>
              <td><input aria-label="Host name" disabled={disabled} value={host.name} onChange={(e) => setHost(i, { ...host, name: e.target.value })} /></td>
              <td style={{ width: 60 }}><input aria-label="Count" type="number" min={1} disabled={disabled} value={host.count ?? 1}
                onChange={(e) => setHost(i, { ...host, count: Number(e.target.value) > 1 ? Number(e.target.value) : undefined })} /></td>
              <td>
                {host.internet ? <span className="gs-muted">internet host (YAML)</span> : (
                  <select aria-label="Segment" disabled={disabled} value={host.segment || ''} onChange={(e) => setHost(i, { ...host, segment: e.target.value })}>
                    {segments.map((s) => <option key={s}>{s}</option>)}
                    {host.segment && !segments.includes(host.segment) && <option>{host.segment}</option>}
                  </select>
                )}
              </td>
              <td>
                <select aria-label="Image" disabled={disabled} value={host.image} onChange={(e) => setHost(i, { ...host, image: e.target.value })}>
                  {images.map((im) => <option key={im.name} value={im.name}>{im.name} ({im.type})</option>)}
                  {!images.some((im) => im.name === host.image) && <option>{host.image}</option>}
                </select>
              </td>
              <td><RolePicker value={host.roles || []} roles={caps?.roles || []} disabled={disabled}
                imageType={images.find((im) => im.name === host.image)?.type}
                onChange={(roles) => setHost(i, { ...host, roles })} /></td>
              <td><input type="checkbox" aria-label="Crucible" disabled={disabled} checked={Boolean(host.crucible)} onChange={(e) => setHost(i, { ...host, crucible: e.target.checked || undefined })} /></td>
              <td>{!disabled && <button className="gs-x" title="Remove the host" onClick={() => onChange({ ...spec, hosts: hosts.filter((_, j) => j !== i) })}>×</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!disabled && (
        <button className="gs-btn ghost sm" style={{ marginTop: 6 }}
          onClick={() => onChange({ ...spec, hosts: [...hosts, { name: `host${hosts.length + 1}`, segment: segments[0], image: images.find((im) => im.type === 'lxc')?.name || images[0]?.name || '' }] })}>
          ＋ Host
        </button>
      )}
      <RoleVariables spec={spec} roles={caps?.roles || []} disabled={disabled} onChange={onChange} />
      <div className="gs-muted" style={{ marginTop: 10, fontSize: 11 }}>
        Link conditions, internet hosts and dual-homed sites are edited in YAML.
      </div>
    </div>
  );
};

/** YAML of the spec; applied when it parses, so an edit in progress never loses the form's state. */
const Yaml: React.FC<{ spec: EnclaveSpec; readOnly: boolean; onChange: (s: EnclaveSpec) => void }> = ({ spec, readOnly, onChange }) => {
  const [text, setText] = useState(() => stringify(spec));
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    // The spec changed elsewhere (the form, a save): show it, unless this text already says it
    try {
      if (JSON.stringify(parse(text)) === JSON.stringify(spec)) return;
    } catch {
      // the text doesn't parse yet; the spec wins
    }
    setText(stringify(spec));
  }, [spec]); // eslint-disable-line react-hooks/exhaustive-deps
  const edit = (next: string) => {
    setText(next);
    try {
      const parsed = parse(next);
      if (!parsed || typeof parsed !== 'object') throw new Error('The spec is a mapping (apiVersion, kind, slug, …)');
      if (parsed.slug !== spec.slug) throw new Error(`slug is ${spec.slug}; save a copy under another name with New enclave`);
      setProblem(null);
      onChange(parsed as EnclaveSpec);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <div>
      <textarea className="gs-yaml" aria-label="YAML" spellCheck={false} readOnly={readOnly} value={text} onChange={(e) => edit(e.target.value)} />
      {problem && <div className="gs-finding bad">{problem}</div>}
    </div>
  );
};

export const EnclaveEditor: React.FC<{ spec: EnclaveSpec; caps: Capabilities | null; readOnly: boolean; onChange: (s: EnclaveSpec) => void }> = (props) => {
  const [tab, setTab] = useState<'form' | 'yaml'>('form');
  return (
    <div>
      <div className="gs-seg" style={{ marginBottom: 8 }}>
        <button className={tab === 'form' ? 'on' : ''} onClick={() => setTab('form')}>Form</button>
        <button className={tab === 'yaml' ? 'on' : ''} onClick={() => setTab('yaml')}>YAML</button>
      </div>
      {tab === 'form' ? <Form {...props} /> : <Yaml spec={props.spec} readOnly={props.readOnly} onChange={props.onChange} />}
    </div>
  );
};

/** A new enclave's starting spec: one site on the first ISP, one host on the first LXC image. */
export const newEnclave = (slug: string, caps: Capabilities | null): EnclaveSpec => ({
  apiVersion: 'grayspace/v1',
  kind: 'Enclave',
  slug,
  prefix: slug.replace(/[^a-z0-9]/g, '').slice(0, 4) || 'e',
  description: '',
  sites: [{ name: 'hq', attach: { isp: caps?.isps[0]?.name, location: caps?.isps[0]?.locations[0] }, segments: [{ name: 'lan' }] }],
  hosts: [{ name: 'ws', segment: 'lan', image: caps?.images.find((im) => im.type === 'lxc')?.name || caps?.images[0]?.name || '' }],
});

export const validSlug = (slug: string): boolean => LABEL.test(slug) && slug.length <= 40;
