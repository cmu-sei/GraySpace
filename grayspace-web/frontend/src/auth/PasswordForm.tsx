import React, { useState } from 'react';
import { control } from '../api/control';

const MIN = 12;

/** Change your own password (POST /api/v1/auth/password); your other sessions end. */
export const PasswordForm: React.FC<{ onChanged: () => void; submitLabel?: string; current?: string }> = ({ onChanged, submitLabel = 'Change password' }) => {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problem = next && next.length < MIN ? `At least ${MIN} characters` : repeat && next !== repeat ? "The passwords don't match" : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await control.changePassword(current, next);
      setCurrent('');
      setNext('');
      setRepeat('');
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <div className="gs-field">
        <label htmlFor="current-password">Current password</label>
        <input id="current-password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
      </div>
      <div className="gs-field">
        <label htmlFor="new-password">New password ({MIN} characters or more)</label>
        <input id="new-password" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
      </div>
      <div className="gs-field">
        <label htmlFor="repeat-password">Repeat it</label>
        <input id="repeat-password" type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
      </div>
      {(problem || error) && <div className="gs-banner bad" role="alert">{problem || error}</div>}
      <button className="gs-btn" type="submit" disabled={busy || !current || !next || next !== repeat || next.length < MIN}>{submitLabel}</button>
    </form>
  );
};
