/** Your password and sessions (POST /api/v1/auth/password, GET/DELETE /api/v1/auth/sessions). */
import React, { useCallback, useEffect, useState } from 'react';
import { control } from '../../api/control';
import { useAuth } from '../../auth/AuthProvider';
import { PasswordForm } from '../../auth/PasswordForm';
import { SessionInfo } from '../../types/control';

export const AccountPage: React.FC = () => {
  const { me } = useAuth();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [changed, setChanged] = useState(false);
  const load = useCallback(() => { control.sessions().then(setSessions).catch(() => setSessions([])); }, []);
  useEffect(load, [load]);
  return (
    <div className="gs-page">
      <h2 className="gs-title">Password &amp; sessions</h2>
      <div className="gs-subtitle">{me?.user?.name} · <span className="gs-mono">{me?.user?.username}</span> · {me?.role}</div>
      <div className="gs-grid2" style={{ alignItems: 'start', maxWidth: 1000 }}>
        <div className="gs-box">
          <h3>Change password</h3>
          {changed && <div className="gs-banner info">Password changed. Your other sessions have ended.</div>}
          <PasswordForm onChanged={() => { setChanged(true); load(); }} />
        </div>
        <div className="gs-box">
          <h3>Sessions</h3>
          <table className="gs-table">
            <thead><tr><th>Signed in</th><th>From</th><th /></tr></thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td>{new Date(s.created).toLocaleString()}{s.current ? ' (this one)' : ''}</td>
                  <td className="gs-mono">{s.ip}</td>
                  <td style={{ textAlign: 'right' }}>{!s.current && <button className="gs-btn ghost sm" onClick={() => control.endSession(s.id).then(load)}>Sign out</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="gs-muted" style={{ marginTop: 8 }}>Personal API tokens come later; scripts use the server's GRAYSPACE_API_TOKEN.</div>
        </div>
      </div>
    </div>
  );
};
