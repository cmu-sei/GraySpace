/** API keys: today the server's one service token; per-user and scoped keys come later. */
import React from 'react';

export const ApiKeysPage: React.FC = () => (
  <div className="gs-box">
    <h3>API keys</h3>
    <table className="gs-table">
      <thead><tr><th>Name</th><th>Who</th><th>May</th><th>Where it is set</th></tr></thead>
      <tbody>
        <tr>
          <td className="gs-mono">GRAYSPACE_API_TOKEN</td><td>the server (service)</td><td><span className="gs-pill info">everything</span></td>
          <td className="gs-muted">the environment file</td>
        </tr>
      </tbody>
    </table>
    <div className="gs-banner info" style={{ marginTop: 10 }}>
      The service token is for scripts and integrations such as GHOSTS. People sign in with their own accounts.
    </div>
  </div>
);
