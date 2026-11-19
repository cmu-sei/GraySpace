/** Local accounts (GET/POST/PATCH/DELETE /api/v1/users): roles, disabling, password resets, and which deployments each is in. */
import React, { useCallback, useEffect, useState } from 'react';
import { control } from '../../api/control';
import { useAuth } from '../../auth/AuthProvider';
import { useDeployments } from '../../shell/DeploymentsProvider';
import { OneTimePassword, Role, User } from '../../types/control';
import { OneTimePasswordModal } from './OneTimePasswordModal';
import { Tip } from '../../components/Tip';

const ROLES: Role[] = ['admin', 'builder', 'user'];
const USERNAME = /^[a-z][a-z0-9._-]{0,31}$/;

const NewUser: React.FC<{ onClose: () => void; onCreated: (r: OneTimePassword) => void }> = ({ onClose, onCreated }) => {
  const { deployments } = useDeployments();
  const [username, setUsername] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('user');
  const [joined, setJoined] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      onCreated(await control.createUser({ username, name, email, role, deployments: joined }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <div className="gs-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="gs-modal" onSubmit={submit}>
        <h3>New local user</h3>
        <div className="gs-grid2">
          <div className="gs-field"><label htmlFor="u-name">Username</label><input id="u-name" className="gs-mono" autoFocus value={username} onChange={(e) => setUsername(e.target.value.toLowerCase())} /></div>
          <div className="gs-field"><label htmlFor="u-full">Name</label><input id="u-full" value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div className="gs-field"><label htmlFor="u-email">Email</label><input id="u-email" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
          <div className="gs-field">
            <label htmlFor="u-role">Role</label>
            <select id="u-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select>
          </div>
        </div>
        {deployments.length > 0 && (
          <div className="gs-field">
            <label>Add to deployments, with use access</label>
            <div className="gs-row" style={{ flexWrap: 'wrap', gap: 10 }}>
              {deployments.map((d) => (
                <label key={d.id} className="gs-row"><input type="checkbox" checked={joined.includes(d.id)}
                  onChange={(e) => setJoined(e.target.checked ? [...joined, d.id] : joined.filter((x) => x !== d.id))} /> <span className="gs-mono">{d.id}</span></label>
              ))}
            </div>
          </div>
        )}
        <div className="gs-banner info">A one-time password is shown once the user is made; they choose their own at first sign-in.</div>
        {error && <div className="gs-banner bad">{error}</div>}
        <div className="gs-actions">
          <button type="button" className="gs-btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="gs-btn" disabled={!USERNAME.test(username)}>Create user</button>
        </div>
      </form>
    </div>
  );
};

export const UsersPage: React.FC = () => {
  const { me } = useAuth();
  const { deployments, refresh } = useDeployments();
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [shown, setShown] = useState<{ result: OneTimePassword; title: string } | null>(null);
  const load = useCallback(() => control.users().then(setUsers).catch((err) => setError(err.message)), []);
  useEffect(() => {
    load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const [search, setSearch] = useState('');
  const needle = search.trim().toLowerCase();
  const listed = needle ? users.filter((u) => [u.username, u.name, u.email].some((v) => v?.toLowerCase().includes(needle))) : users;
  const memberOf = (u: string) => deployments.filter((d) => d.owner === u || d.members.some((m) => m.user === u)).map((d) => d.id);

  return (
    <div className="gs-box">
      <h3>Users <span className="gs-spacer" />
        <input className="gs-input" style={{ width: 220 }} placeholder="Search name, username, email" aria-label="Search users" value={search} onChange={(e) => setSearch(e.target.value)} />
        <button className="gs-btn sm" onClick={() => setCreating(true)}>＋ Local user</button></h3>
      {error && <div className="gs-banner bad">{error}</div>}
      <table className="gs-table">
        <thead><tr><th>Username</th><th>Name</th><th>Role <Tip text="Admin: everything. Builder: creates and builds deployments. User: uses the deployments they're a member of." /></th><th>Source</th><th>Deployments</th><th>Last seen</th><th /></tr></thead>
        <tbody>
          {listed.map((u) => {
            const self = u.username === me?.user?.username;
            return (
              <tr key={u.username} style={u.disabled ? { opacity: 0.55 } : undefined}>
                <td className="gs-mono">{u.username}</td>
                <td>{u.name}{u.email && <div className="gs-muted" style={{ fontSize: 11 }}>{u.email}</div>}</td>
                <td>
                  <select className="gs-input" style={{ width: 100 }} aria-label={`${u.username} role`} value={u.role} disabled={self || u.disabled}
                    onChange={(e) => act(() => control.updateUser(u.username, { role: e.target.value as Role }))}>
                    {ROLES.map((r) => <option key={r}>{r}</option>)}
                  </select>
                </td>
                <td>
                  <span className="gs-pill">{u.source}</span>
                  {u.disabled && <> <span className="gs-pill bad">disabled</span></>}
                  {u.must_change_password && !u.disabled && <> <span className="gs-pill warn">one-time password</span></>}
                </td>
                <td>{memberOf(u.username).map((d) => <span key={d} className="gs-tag" style={{ marginRight: 3 }}>{d}</span>)}</td>
                <td className="gs-muted">{u.last_seen ? new Date(u.last_seen).toLocaleString() : 'never'}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                  {self ? <span className="gs-muted">you</span> : (
                    <>
                      <button className="gs-btn ghost sm" title="Gives a one-time password and signs them out" onClick={() => act(async () => setShown({ result: await control.resetPassword(u.username), title: 'Password reset' }))}>Reset password</button>{' '}
                      <button className="gs-btn ghost sm" onClick={() => act(() => control.updateUser(u.username, { disabled: !u.disabled }))}>{u.disabled ? 'Enable' : 'Disable'}</button>{' '}
                      <button className="gs-btn ghost sm" onClick={() => window.confirm(`Delete ${u.username}? They leave every deployment.`) && act(async () => { await control.deleteUser(u.username); await refresh(); })}>Delete</button>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {needle && listed.length === 0 && <div className="gs-empty">No users match.</div>}
      {creating && <NewUser onClose={() => setCreating(false)} onCreated={(r) => { setCreating(false); setShown({ result: r, title: 'User created' }); load(); refresh(); }} />}
      {shown && <OneTimePasswordModal result={shown.result} title={shown.title} onClose={() => setShown(null)} />}
    </div>
  );
};
