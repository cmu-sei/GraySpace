/** The images and roles enclave hosts can use (GET /images, /roles), one deployment's at a time: each node has its own downloads. */
import React, { useCallback, useEffect, useState } from 'react';
import { control } from '../../api/control';
import { useDeployments } from '../../shell/DeploymentsProvider';
import { CatalogImage, CatalogRole } from '../../types/slices';

export const CatalogPage: React.FC = () => {
  const { deployments } = useDeployments();
  const running = deployments.filter((d) => d.worker.state === 'running');
  const [id, setId] = useState<string>('');
  const [images, setImages] = useState<CatalogImage[]>([]);
  const [roles, setRoles] = useState<CatalogRole[]>([]);
  const [error, setError] = useState<string | null>(null);
  const chosen = id || running[0]?.id || '';
  const load = useCallback(async () => {
    if (!chosen) return;
    try {
      const [i, r] = await Promise.all([control.images(chosen), control.roles(chosen)]);
      setImages(i);
      setRoles(r);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [chosen]);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <div className="gs-row" style={{ marginBottom: 10 }}>
        <span className="gs-muted">Deployment</span>
        <select className="gs-input" style={{ width: 200 }} value={chosen} onChange={(e) => setId(e.target.value)} aria-label="Deployment">
          {running.map((d) => <option key={d.id} value={d.id}>{d.id}{d.name !== d.id ? ` · ${d.name}` : ''}</option>)}
        </select>
      </div>
      {error && <div className="gs-banner bad">{error}</div>}
      <div className="gs-grid2" style={{ alignItems: 'start' }}>
        <div className="gs-box">
          <h3>Images</h3>
          <table className="gs-table">
            <thead><tr><th>Image</th><th>Kind</th><th>OS</th><th>Origin</th><th /></tr></thead>
            <tbody>{images.map((im) => (
              <tr key={im.name}>
                <td><span className="gs-mono">{im.name}</span>{im.description && <div className="gs-muted" style={{ fontSize: 11 }}>{im.description}</div>}</td>
                <td>{im.type}</td><td>{im.os_family}</td><td><span className={`gs-pill ${im.origin === 'local' ? 'info' : ''}`}>{im.origin}</span></td>
                <td>{im.origin === 'local' && <button className="gs-btn ghost sm"
                  onClick={() => window.confirm(`Remove ${im.name} from ${chosen}'s catalog?`) && control.deleteImage(chosen, im.name).then(load).catch((err) => setError(err.message))}>Remove</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <div className="gs-box">
          <h3>Roles</h3>
          <table className="gs-table">
            <thead><tr><th>Role</th><th>For</th><th>Variables</th></tr></thead>
            <tbody>{roles.map((r) => (
              <tr key={r.name}>
                <td><span className="gs-mono">{r.name}</span><div className="gs-muted" style={{ fontSize: 11 }}>{r.description}</div></td>
                <td>{r.os_families.join(', ')}{r.image_types?.length ? ` (${r.image_types.map((t) => (t === 'vm' ? 'VM' : t)).join(' or ')} only)` : ''}</td>
                <td className="gs-mono" style={{ fontSize: 11 }}>{Object.keys(r.variables).join(', ')}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </div>
    </>
  );
};
