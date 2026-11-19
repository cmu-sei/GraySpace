/**
 * A base version before it is deployed (GET /base/preview): its routers and links on a read-only
 * map, and its ISPs with their routers and locations. Loaded only when opened (Leaflet is large).
 */
import React, { useEffect, useMemo, useState } from 'react';
import { MapView } from '../../components/MapView';
import { apiService } from '../../services/api';
import { FeatureCollection } from '../../types/slices';
import { featuresOfKind } from '../../utils/mapLayer';

const BasePreview: React.FC<{ version: string; scope: string; onClose: () => void }> = ({ version, scope, onClose }) => {
  const [layer, setLayer] = useState<FeatureCollection | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiService.getBasePreview(version, scope).then(setLayer).catch((err) => setError(err.message));
  }, [version, scope]);

  const isps = useMemo(() => {
    const out = new Map<string, { asn?: number; color?: string; routers: number; locations: Set<string> }>();
    for (const r of featuresOfKind(layer, 'router')) {
      const p = r.properties;
      const isp = out.get(p.isp ?? '') ?? { asn: p.asn ?? undefined, color: p.color ?? undefined, routers: 0, locations: new Set<string>() };
      isp.routers += 1;
      if (p.location) isp.locations.add(p.location);
      out.set(p.isp ?? '', isp);
    }
    return Array.from(out.entries());
  }, [layer]);
  const links = featuresOfKind(layer, 'link').length;

  return (
    <div className="gs-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="gs-modal gs-modal-wide" role="dialog" aria-label={`Preview of ${version}`}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{version} <span className="gs-muted" style={{ fontWeight: 400 }}>{scope === 'full' ? 'Tier 1 and 2' : 'Tier 1 only'}</span>
          <span className="gs-spacer" /><button className="gs-btn ghost sm" onClick={onClose}>Close</button></h3>
        {error && <div className="gs-banner bad">{error}</div>}
        {!layer && !error && <div className="gs-empty">Loading the map…</div>}
        {layer && (
          <div className="gs-preview">
            <div className="gs-preview-map"><MapView layer={layer} onRouterSelect={() => undefined} /></div>
            <div className="gs-preview-side">
              <div className="gs-muted" style={{ marginBottom: 6 }}>{isps.length} ISPs · {isps.reduce((n, [, i]) => n + i.routers, 0)} routers · {links} links</div>
              <table className="gs-table">
                <thead><tr><th>ISP</th><th>ASN</th><th>Routers</th><th>Locations</th></tr></thead>
                <tbody>{isps.map(([name, i]) => (
                  <tr key={name}>
                    <td><span className="gs-dot" style={{ background: i.color, marginRight: 6 }} />{name}</td>
                    <td className="gs-mono">{i.asn}</td><td>{i.routers}</td><td>{i.locations.size}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default BasePreview;
