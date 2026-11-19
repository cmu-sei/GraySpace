/** Every write through the server and every sign-in, newest first (GET /api/v1/audit). */
import React, { useCallback, useEffect, useState } from 'react';
import { control } from '../../api/control';
import { AuditEntry } from '../../types/control';

export const AuditPage: React.FC = () => {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [user, setUser] = useState('');
  const [deployment, setDeployment] = useState('');
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => control.audit({ user: user || undefined, deployment: deployment || undefined, limit: 300 })
    .then((e) => { setEntries(e); setError(null); }).catch((err) => setError(err.message)), [user, deployment]);
  useEffect(() => {
    load();
  }, [load]);
  return (
    <div className="gs-box">
      <h3>
        Audit log <span className="gs-spacer" />
        <input className="gs-input" style={{ width: 150 }} placeholder="user" value={user} onChange={(e) => setUser(e.target.value)} aria-label="User" />
        <input className="gs-input" style={{ width: 150 }} placeholder="deployment" value={deployment} onChange={(e) => setDeployment(e.target.value)} aria-label="Deployment" />
        <button className="gs-btn ghost sm" onClick={load}>Refresh</button>
      </h3>
      {error && <div className="gs-banner bad">{error}</div>}
      <table className="gs-table">
        <thead><tr><th>When</th><th>Who</th><th>Deployment</th><th>What</th><th>Status</th><th>From</th></tr></thead>
        <tbody>{entries.map((e, i) => (
          <tr key={`${e.at}-${i}`}>
            <td className="gs-muted" style={{ whiteSpace: 'nowrap' }}>{new Date(e.at).toLocaleString()}</td>
            <td>{e.user || <span className="gs-muted">nobody</span>}</td>
            <td>{e.deployment && <span className="gs-tag">{e.deployment}</span>}</td>
            <td className="gs-mono" style={{ fontSize: 11 }}>{e.method} {e.path}{e.detail && <span className="gs-muted"> · {e.detail}</span>}</td>
            <td><span className={`gs-pill ${e.status >= 400 ? 'bad' : 'ok'}`}>{e.status}</span></td>
            <td className="gs-mono gs-muted" style={{ fontSize: 11 }}>{e.ip}</td>
          </tr>
        ))}</tbody>
      </table>
      {!entries.length && !error && <div className="gs-empty">Nothing recorded{user || deployment ? ' that matches' : ' yet'}.</div>}
    </div>
  );
};
