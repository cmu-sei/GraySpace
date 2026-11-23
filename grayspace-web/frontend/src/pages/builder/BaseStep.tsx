/** Step 1: which shipped base version (GET /base/versions), its scope and preset, and whether this site can deploy it (GET /base/preflight). */
import React, { Suspense, lazy, useEffect, useState } from 'react';
import { apiService } from '../../services/api';
import { PresetSummary } from '../../types';
import { Preflight } from '../../types/build';
import { Tip } from '../../components/Tip';
import { ReadinessChecks } from './ReadinessChecks';
import { Builder } from './useBuilder';

// Leaflet loads only when a preview opens
const BasePreview = lazy(() => import('./BasePreview'));

export const BaseStep: React.FC<{ b: Builder; readOnly: boolean }> = ({ b, readOnly }) => {
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [presets, setPresets] = useState<PresetSummary[]>([]);
  const [previewing, setPreviewing] = useState<string | null>(null);
  const base = b.base;

  // The version's presets, saved from any deployment on it
  useEffect(() => {
    const version = base?.version;
    if (!version) return;
    let stopped = false;
    apiService.getBasePresets(version).then((p) => !stopped && setPresets(p)).catch(() => !stopped && setPresets([]));
    return () => {
      stopped = true;
    };
  }, [base?.version]);

  const check = async () => {
    if (!base) return;
    setChecking(true);
    setError(null);
    try {
      setPreflight(await apiService.getPreflight(base.version, base.scope));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setChecking(false);
    }
  };
  useEffect(() => {
    check();
  }, [base?.version, base?.scope]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="gs-grid2" style={{ gridTemplateColumns: '1.3fr 1fr', alignItems: 'start' }}>
      <div>
        <div className="gs-box">
          <h3>Core version</h3>
          {b.versions.map((v) => (
            <label key={v.name} className={`gs-choice ${base?.version === v.name ? 'on' : ''}`}>
              <div className="gs-row">
                <input type="radio" name="version" disabled={readOnly || v.problems.length > 0} checked={base?.version === v.name}
                  onChange={() => b.setBase({ version: v.name, scope: base?.scope ?? 'full', configure: base?.configure ?? true, preset: null })} />
                <b>{v.name}</b>
                {v.deployed && <span className="gs-pill info">deployed</span>}
                {v.problems.length > 0 && <span className="gs-pill bad">doesn't load</span>}
                <span className="gs-spacer" />
                {v.problems.length === 0 && (
                  <button className="gs-btn ghost sm" onClick={(e) => { e.preventDefault(); setPreviewing(v.name); }}>Preview</button>
                )}
              </div>
              {v.summary && (
                <div className="gs-row" style={{ flexWrap: 'wrap', gap: 5, marginTop: 6 }}>
                  <span className="gs-tag">{v.summary.routers} routers</span><span className="gs-tag">{v.summary.isps} ISPs</span>
                  <span className="gs-tag">{v.summary.links} links</span><span className="gs-tag">{v.summary.locations} locations</span>
                  <span className="gs-tag">{v.summary.core_services} core services</span>
                </div>
              )}
              {v.problems.map((p) => <div key={p} className="gs-muted" style={{ fontSize: 11 }}>{p}</div>)}
            </label>
          ))}
        </div>
        <div className="gs-box">
          <h3>Options</h3>
          <div className="gs-row" style={{ gap: 16, flexWrap: 'wrap' }}>
            <div className="gs-seg" role="radiogroup" aria-label="Scope">
              {(['full', 'tier1'] as const).map((s) => (
                <button key={s} disabled={readOnly || !base} className={base?.scope === s ? 'on' : ''} onClick={() => base && b.setBase({ ...base, scope: s })}>
                  {s === 'full' ? 'Full (Tier 1 and 2)' : 'Tier 1 only'}
                </button>
              ))}
            </div>
            <label className="gs-row">
              <input type="checkbox" disabled={readOnly || !base} checked={base?.configure ?? true} onChange={(e) => base && b.setBase({ ...base, configure: e.target.checked })} />
              Configure routing and services
            </label>
          </div>
          {(presets.length > 0 || base?.preset) && (
            <div className="gs-row" style={{ marginTop: 10 }}>
              <label htmlFor="base-preset">Preset <Tip text="A saved set of changes to the core, applied once it's deployed." /></label>
              <select id="base-preset" className="gs-input" style={{ width: 'auto' }} disabled={readOnly || !base} value={base?.preset ?? ''}
                onChange={(e) => base && b.setBase({ ...base, preset: e.target.value || null })}>
                <option value="">None</option>
                {presets.map((p) => <option key={p.name} value={p.name}>{p.name}{p.description ? ` · ${p.description}` : ''}</option>)}
              </select>
            </div>
          )}
        </div>
      </div>
      <div className="gs-box">
        <h3>Readiness checks <span className="gs-spacer" /><button className="gs-btn ghost sm" onClick={check} disabled={checking || !base}>{checking ? 'Checking…' : 'Check again'}</button></h3>
        {error && <div className="gs-banner bad">{error}</div>}
        {preflight && <ReadinessChecks preflight={preflight} />}
        {preflight && <div className={`gs-finding ${preflight.ready ? 'ok' : 'bad'}`} style={{ marginTop: 8 }}>{preflight.ready ? 'Ready to deploy' : 'Fix the errors before deploying'}</div>}
      </div>
      {previewing && (
        <Suspense fallback={null}>
          <BasePreview version={previewing} scope={base?.version === previewing ? base.scope : 'full'} onClose={() => setPreviewing(null)} />
        </Suspense>
      )}
    </div>
  );
};
