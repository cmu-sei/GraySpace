/**
 * Step 3: DNS. The base's servers are the base's (read-only); each chosen enclave's domains (on a
 * hosting server of the base) and records are its spec's dns section.
 */
import React from 'react';
import { DnsRecord, EnclaveSpec } from '../../types/build';
import { Builder } from './useBuilder';

const TYPES: NonNullable<DnsRecord['type']>[] = ['A', 'CNAME', 'TXT', 'MX'];

export const DnsStep: React.FC<{ b: Builder; readOnly: boolean }> = ({ b, readOnly }) => {
  const caps = b.capabilities;
  const chosen = b.enclaves.map((e) => b.specs[e.slug]).filter(Boolean) as EnclaveSpec[];
  const hosting = caps?.dns.hosting || [];
  const setDns = (spec: EnclaveSpec, dns: EnclaveSpec['dns']) => b.updateSpec({ ...spec, dns });

  return (
    <div>
      <div className="gs-box">
        <h3>The core's DNS <span className="gs-pill">read-only</span></h3>
        <div className="gs-row" style={{ flexWrap: 'wrap', gap: 5 }}>
          <span className="gs-muted">TLDs</span>{(caps?.dns.tlds || []).map((t) => <span key={t} className="gs-tag">.{t}</span>)}
        </div>
        <div className="gs-row" style={{ flexWrap: 'wrap', gap: 5, marginTop: 6 }}>
          <span className="gs-muted">Hosting servers</span>{hosting.map((h) => <span key={h.hostname} className="gs-tag">{h.hostname}</span>)}
        </div>
        <div className="gs-row" style={{ flexWrap: 'wrap', gap: 5, marginTop: 6 }}>
          <span className="gs-muted">Resolvers</span>{(caps?.dns.resolvers || []).map((r) => <span key={r.hostname} className="gs-tag">{r.hostname}</span>)}
        </div>
      </div>
      {chosen.length === 0 && <div className="gs-empty">No enclaves chosen: their domains and records go here.</div>}
      {chosen.map((spec) => {
        const locked = readOnly || b.readOnly(spec.slug);
        const domains = spec.dns?.domains || [];
        const records = spec.dns?.records || [];
        const hosts = (spec.hosts || []).map((h) => h.name);
        const setRecord = (i: number, r: DnsRecord) => setDns(spec, { ...spec.dns, records: records.map((x, j) => (j === i ? r : x)) });
        // A record goes under the longest of the enclave's domains it is inside
        const domainOf = (name: string) => domains.map((d) => d.domain).filter((d) => d && (name === d || name.endsWith(`.${d}`)))
          .sort((x, y) => y.length - x.length)[0];
        const indexed = records.map((r, i) => ({ r, i }));
        const outside = indexed.filter(({ r }) => !domainOf(r.name));
        const renameDomain = (i: number, next: string) => {
          const old = domains[i].domain;
          const moved = (name: string) => (old && domainOf(name) === old ? name.slice(0, name.length - old.length) + next : name);
          setDns(spec, { ...spec.dns, domains: domains.map((x, j) => (j === i ? { ...x, domain: next } : x)), records: records.map((r) => ({ ...r, name: moved(r.name) })) });
        };
        const addRecord = (domain: string) => {
          const taken = new Set(records.map((r) => r.name));
          const name = [`www.${domain}`, ...hosts.map((h) => `${h}.${domain}`)].find((n) => !taken.has(n)) || `new.${domain}`;
          setDns(spec, { ...spec.dns, records: [...records, { name, type: 'A', host: hosts[0] }] });
        };
        const table = (rows: { r: DnsRecord; i: number }[]) => (
          <table className="gs-table gs-form-table">
            <thead><tr><th>Name</th><th>Type</th><th>Value</th><th>TTL</th><th /></tr></thead>
            <tbody>{rows.map(({ r, i }) => {
              const type = r.type || 'A';
              return (
                <tr key={i}>
                  <td><input aria-label="Record name" className="gs-mono" disabled={locked} value={r.name} onChange={(e) => setRecord(i, { ...r, name: e.target.value.toLowerCase() })} /></td>
                  <td style={{ width: 80 }}>
                    <select aria-label="Type" disabled={locked} value={type}
                      onChange={(e) => {
                        const next = e.target.value as DnsRecord['type'];
                        setRecord(i, next === 'A' ? { name: r.name, type: next, host: hosts[0], ttl: r.ttl } : { name: r.name, type: next, data: r.data || '', ttl: r.ttl });
                      }}>
                      {TYPES.map((t) => <option key={t}>{t}</option>)}
                    </select>
                  </td>
                  <td>
                    {type === 'A' && r.host !== undefined ? (
                      <select aria-label="Host" disabled={locked} value={r.host} onChange={(e) => setRecord(i, { ...r, host: e.target.value })}>
                        {hosts.map((h) => <option key={h}>{h}</option>)}
                      </select>
                    ) : (
                      <input aria-label="Data" className="gs-mono" disabled={locked} value={r.data || ''} placeholder={type === 'MX' ? '10 mail.example.com' : ''}
                        onChange={(e) => setRecord(i, { ...r, data: e.target.value, host: undefined })} />
                    )}
                  </td>
                  <td style={{ width: 70 }}><input aria-label="TTL" type="number" disabled={locked} value={r.ttl ?? 3600}
                    onChange={(e) => setRecord(i, { ...r, ttl: Number(e.target.value) === 3600 ? undefined : Number(e.target.value) })} /></td>
                  <td>{!locked && <button className="gs-x" title="Remove" onClick={() => setDns(spec, { ...spec.dns, records: records.filter((_, j) => j !== i) })}>×</button>}</td>
                </tr>
              );
            })}</tbody>
          </table>
        );
        return (
          <div key={spec.slug} className="gs-box">
            <h3><span className="gs-mono">{spec.slug}</span> <span className="gs-spacer" />
              {!locked && <button className="gs-btn ghost sm"
                onClick={() => setDns(spec, { ...spec.dns, domains: [...domains, { domain: `${spec.slug}.com`, hosting_server: hosting[0]?.hostname || '' }] })}>＋ Domain</button>}
            </h3>
            {domains.length === 0 && <div className="gs-muted">No domains.</div>}
            {domains.map((d, i) => {
              const rows = indexed.filter(({ r }) => domainOf(r.name) === d.domain);
              return (
                <div key={i} className="gs-domain">
                  <div className="gs-row">
                    <input aria-label="Domain" className="gs-mono" style={{ width: 240 }} disabled={locked} value={d.domain}
                      onChange={(e) => renameDomain(i, e.target.value.toLowerCase())} />
                    <label className="gs-row gs-muted" style={{ gap: 4 }}>
                      on
                      <select aria-label="Hosting server" disabled={locked} value={d.hosting_server}
                        onChange={(e) => setDns(spec, { ...spec.dns, domains: domains.map((x, j) => (j === i ? { ...x, hosting_server: e.target.value } : x)) })}>
                        {hosting.map((h) => <option key={h.hostname}>{h.hostname}</option>)}
                        {!hosting.some((h) => h.hostname === d.hosting_server) && <option>{d.hosting_server}</option>}
                      </select>
                    </label>
                    <span className="gs-spacer" />
                    {!locked && <button className="gs-x" title="Remove the domain" onClick={() => setDns(spec, { ...spec.dns, domains: domains.filter((_, j) => j !== i) })}>×</button>}
                  </div>
                  {rows.length > 0 && table(rows)}
                  {!locked && <button className="gs-btn ghost sm" style={{ marginTop: 6 }} disabled={!d.domain} onClick={() => addRecord(d.domain)}>＋ Record</button>}
                </div>
              );
            })}
            {outside.length > 0 && (
              <div className="gs-domain">
                <div className="gs-muted">Records outside the enclave's domains</div>
                {table(outside)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};
