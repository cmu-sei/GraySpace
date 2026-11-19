import React from 'react';
import { CopyButton } from '../../components/CopyButton';
import { OneTimePassword } from '../../types/control';

/** A one-time password, shown once: the user changes it at first sign-in. */
export const OneTimePasswordModal: React.FC<{ result: OneTimePassword; title: string; onClose: () => void }> = ({ result, title, onClose }) => {
  return (
    <div className="gs-modal-bg">
      <div className="gs-modal" role="dialog" aria-label={title}>
        <h3>{title}</h3>
        <p>Give <b className="gs-mono">{result.user.username}</b> this one-time password. It is shown once; they choose their own at first sign-in.</p>
        <div className="gs-otp gs-mono" data-testid="one-time-password">{result.one_time_password}</div>
        <div className="gs-actions" style={{ flexWrap: 'wrap' }}>
          <CopyButton text={result.one_time_password} label="Copy" className="gs-btn ghost" />
          <button className="gs-btn" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
};
