/** Step 4: certificates. The base's CAs are the base's (read-only); each chosen enclave's certificates are its spec's tls section. */
import React, { useState } from 'react';
import { apiService } from '../../services/api';
import { EnclaveSpec, Tls } from '../../types/build';
import { Tip } from '../../components/Tip';
import { toList } from './parts';
import { Builder } from './useBuilder';

export const CertificatesStep: React.FC<{ b: Builder; readOnly: boolean; live: boolean }> = ({ b, readOnly, live }) => {
  const cas = b.capabilities?.cas || [];
  const chosen = b.enclaves.map((e) => b.specs[e.slug]).filter(Boolean) as EnclaveSpec[];
  const [error, setError] = useState<string | null>(null);

  const download = async () => {
    try {
      const pem = await apiService.getCABundle();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([pem], { type: 'application/x-pem-file' }));
      a.download = 'grayspace-ca-bundle.pem';
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div>
      <div className="gs-box">
        <h3>The base's CAs <span className="gs-pill">read-only</span><span className="gs-spacer" />
          {live && <button className="gs-btn ghost sm" onClick={download}>Download the CA bundle</button>}</h3>
        <div className="gs-row" style={{ flexWrap: 'wrap', gap: 5 }}>
          {cas.map((c) => <span key={c.hostname} className="gs-tag">{c.name} · {c.hostname}</span>)}
        </div>
        {error && <div className="gs-banner bad" style={{ marginTop: 8 }}>{error}</div>}
      </div>
      {chosen.length === 0 && <div className="gs-empty">No enclaves chosen: the certificates their hosts serve go here.</div>}
      {chosen.map((spec) => {
        const locked = readOnly || b.readOnly(spec.slug);
        const tls = spec.tls || [];
        const hosts = (spec.hosts || []).map((h) => h.name);
        const setTls = (next: Tls[]) => b.updateSpec({ ...spec, tls: next });
        return (
          <div key={spec.slug} className="gs-box">
            <h3><span className="gs-mono">{spec.slug}</span></h3>
            <table className="gs-table gs-form-table">
              <thead><tr><th>Host <Tip text="The host that serves the certificate" /></th><th>Names <Tip text="Comma-separated DNS names the certificate covers; the first is also its common name" /></th><th>CA <Tip text="The simulated CA that signs it" /></th><th /></tr></thead>
              <tbody>{tls.map((t, i) => (
                <tr key={i}>
                  <td style={{ width: 160 }}>
                    <select aria-label="Host" disabled={locked} value={t.host} onChange={(e) => setTls(tls.map((x, j) => (j === i ? { ...x, host: e.target.value } : x)))}>
                      {hosts.map((h) => <option key={h}>{h}</option>)}
                    </select>
                  </td>
                  <td><input aria-label="Names" className="gs-mono" disabled={locked} defaultValue={t.names.join(', ')} key={t.names.join(',')}
                    onBlur={(e) => setTls(tls.map((x, j) => (j === i ? { ...x, names: toList(e.target.value) } : x)))} /></td>
                  <td style={{ width: 200 }}>
                    <select aria-label="CA" disabled={locked} value={t.ca} onChange={(e) => setTls(tls.map((x, j) => (j === i ? { ...x, ca: e.target.value } : x)))}>
                      {cas.map((c) => <option key={c.hostname} value={c.hostname}>{c.name} ({c.hostname})</option>)}
                      {!cas.some((c) => c.hostname === t.ca) && <option>{t.ca}</option>}
                    </select>
                  </td>
                  <td>{!locked && <button className="gs-x" title="Remove" onClick={() => setTls(tls.filter((_, j) => j !== i))}>×</button>}</td>
                </tr>
              ))}</tbody>
            </table>
            {!locked && <button className="gs-btn ghost sm" style={{ marginTop: 6 }}
              onClick={() => setTls([...tls, { host: hosts[0] || '', names: [spec.dns?.domains?.[0] ? `www.${spec.dns.domains[0].domain}` : ''].filter(Boolean), ca: cas[0]?.hostname || '' }])}>
              ＋ Certificate
            </button>}
          </div>
        );
      })}
    </div>
  );
};
