/**
 * The map's preset box: the base preset that is active, the changes made since it (and who made
 * them), Reset changes, and the base version's presets to apply, save, download and delete. A
 * preset belongs to the base version, so every deployment on it sees the same ones.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { apiService } from '../services/api';
import { ApplyPresetResult, Preset, PresetSummary, TrackedChanges } from '../types';
import { ago } from '../pages/jobs/format';

const NAME = /^[a-z0-9][a-z0-9-]{0,47}$/;

const KIND_LABEL: Record<string, string> = {
  'link.condition': 'link', 'interface.zone': 'zone', 'dns.domain.create': 'domain', 'dns.record.set': 'record',
};

export const kindsText = (kinds: Record<string, number>) =>
  Object.entries(kinds).map(([k, n]) => `${n} ${KIND_LABEL[k] ?? k}${n === 1 ? '' : 's'}`).join(', ') || 'no changes';

const download = (preset: Preset) => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(preset, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `${preset.owner.ref.replace('/', '-')}-${preset.name}.json`;
  a.click();
  URL.revokeObjectURL(url);
};

interface Props {
  onClose: () => void;
  /** Called after anything that changes the active preset or the changes */
  onChanged: () => void;
  canOperate: boolean;
  canBuild: boolean;
}

export const PresetModal: React.FC<Props> = ({ onClose, onChanged, canOperate, canBuild }) => {
  const [changes, setChanges] = useState<TrackedChanges | null>(null);
  const [list, setList] = useState<PresetSummary[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ApplyPresetResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const upload = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const c = await apiService.getChanges();
      setChanges(c);
      setList(c.base ? await apiService.getBasePresets() : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const run = async (label: string, fn: () => Promise<ApplyPresetResult | void>) => {
    setBusy(label);
    setError(null);
    setResult(null);
    try {
      const r = await fn();
      if (r) setResult(r);
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const active = changes?.preset ?? null;
  const since = changes?.changes ?? [];
  const willReset = (target: string) =>
    !since.length || window.confirm(`Applying ${target} first resets the ${since.length} change${since.length === 1 ? '' : 's'} made since ${active ?? 'the base'}. Go on?`);

  const apply = (preset: string | null) => {
    if (!willReset(preset ?? 'no preset')) return;
    run(`apply:${preset ?? ''}`, () => apiService.applyBasePreset(preset));
  };
  const save = () => {
    if (list.some((p) => p.name === name) && !window.confirm(`Replace the preset ${name} for every deployment on ${changes?.base}?`)) return;
    run('save', async () => {
      await apiService.saveBasePreset(name, description);
      setName('');
      setDescription('');
    });
  };
  const remove = (preset: string) => {
    if (!window.confirm(`Delete the preset ${preset} for every deployment on ${changes?.base}?`)) return;
    run(`delete:${preset}`, () => apiService.deleteBasePreset(preset));
  };
  const onUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    run('upload', async () => {
      const preset = JSON.parse(await file.text()) as Preset;
      await apiService.putBasePreset(preset);
    });
  };

  return (
    <div className="gs-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="gs-modal" style={{ width: 720 }} role="dialog" aria-label="Presets">
        <h3 className="gs-row">Presets{changes?.base && <span className="gs-muted" style={{ fontWeight: 400 }}>· {changes.base}</span>}<span className="gs-spacer" />
          <button className="gs-btn ghost sm" onClick={onClose} aria-label="Close">✕</button></h3>
        <div className="gs-muted" style={{ marginBottom: 10 }}>
          A preset is a saved set of changes to the base: link conditions, interface zones, DNS domains and records.
          It belongs to the base version, so every deployment on {changes?.base ?? 'it'} can apply it. Enclaves have their own, on their page.
        </div>
        {error && <div className="gs-banner bad">{error}</div>}
        {result && (
          <div className={`gs-banner ${result.errors.length ? 'bad' : result.warnings.length ? 'warn' : 'info'}`}>
            <div>
              <b>{result.message}</b>
              {[...result.errors, ...result.warnings].map((m) => <div key={m} style={{ fontSize: 11 }}>{m}</div>)}
            </div>
          </div>
        )}

        <div className="gs-box">
          <h3 className="gs-row">
            Active: {active ? <span className="gs-mono">{active}</span> : <span>none <span className="gs-muted" style={{ fontWeight: 400 }}>(the base as deployed)</span></span>}
            <span className="gs-spacer" />
            {canOperate && (
              <button className="gs-btn ghost sm" disabled={Boolean(busy) || !since.length} title={`Undo the changes since ${active ?? 'the base'}`}
                onClick={() => window.confirm(`Undo the ${since.length} change${since.length === 1 ? '' : 's'} since ${active ?? 'the base'}?`) && run('discard', () => apiService.discardChanges())}>
                {busy === 'discard' ? 'Resetting…' : 'Reset changes'}
              </button>
            )}
          </h3>
          {since.length === 0 ? <div className="gs-muted">No changes since {active ?? 'the base'}.</div> : (
            <table className="gs-table">
              <tbody>{since.map((c) => (
                <tr key={`${c.kind}-${c.key}`}>
                  <td>{c.summary}</td>
                  <td className="gs-muted" style={{ whiteSpace: 'nowrap' }}>{c.by ?? 'service token'} · {ago(c.at)}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </div>

        {canBuild && changes?.base && (
          <div className="gs-box">
            <h3>Save as a preset</h3>
            <div className="gs-row">
              <input className="gs-input gs-mono" style={{ width: 180 }} placeholder="name, e.g. slow-links" aria-label="Preset name" value={name}
                onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-'))} />
              <input className="gs-input" style={{ flex: 1 }} placeholder="Description (optional)" aria-label="Preset description" value={description}
                onChange={(e) => setDescription(e.target.value)} />
              <button className="gs-btn sm" disabled={Boolean(busy) || !NAME.test(name)} onClick={save}>
                {busy === 'save' ? 'Saving…' : list.some((p) => p.name === name) ? 'Replace' : 'Save'}
              </button>
            </div>
            <div className="gs-muted" style={{ fontSize: 11, marginTop: 4 }}>
              Saves every change on the base{active ? `, ${active}'s included` : ''}. It becomes the active preset.
            </div>
          </div>
        )}

        <div className="gs-box">
          <h3 className="gs-row">{changes?.base ? `Presets for ${changes.base}` : 'Presets'}<span className="gs-spacer" />
            {canBuild && changes?.base && <>
              <button className="gs-btn ghost sm" disabled={Boolean(busy)} onClick={() => upload.current?.click()}>Upload…</button>
              <input ref={upload} type="file" accept=".json,application/json" style={{ display: 'none' }} onChange={onUpload} aria-label="Upload a preset" />
            </>}
          </h3>
          {!changes?.base ? <div className="gs-muted">No base version is deployed: presets belong to one.</div> : (
            <table className="gs-table">
              <tbody>
                <tr>
                  <td><b>None</b>{active === null && <span className="gs-pill info" style={{ marginLeft: 6 }}>active</span>}<div className="gs-muted" style={{ fontSize: 11 }}>The base as deployed, no changes</div></td>
                  <td />
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {canOperate && <button className="gs-btn ghost sm" disabled={Boolean(busy) || (active === null && !since.length)} onClick={() => apply(null)}>
                      {busy === 'apply:' ? 'Applying…' : 'Apply'}</button>}
                  </td>
                </tr>
                {list.map((p) => (
                  <tr key={p.name}>
                    <td>
                      <span className="gs-mono">{p.name}</span>{active === p.name && <span className="gs-pill info" style={{ marginLeft: 6 }}>active</span>}
                      {p.description && <div className="gs-muted" style={{ fontSize: 11 }}>{p.description}</div>}
                      {p.problems.length > 0 && <div className="gs-muted" style={{ fontSize: 11, color: 'var(--gs-warn)' }} title={p.problems.join('\n')}>
                        {p.problems.length} target{p.problems.length === 1 ? '' : 's'} this deployment doesn't have: skipped</div>}
                    </td>
                    <td className="gs-muted" style={{ fontSize: 11 }}>{kindsText(p.kinds)}<div>{p.created_by ?? 'service token'} · {ago(p.created)}</div></td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      {canOperate && <button className="gs-btn ghost sm" disabled={Boolean(busy)} onClick={() => apply(p.name)}>
                        {busy === `apply:${p.name}` ? 'Applying…' : active === p.name ? 'Apply again' : 'Apply'}</button>}{' '}
                      <button className="gs-btn ghost sm" onClick={() => apiService.getBasePreset(p.name).then(download).catch((err) => setError(String(err)))}>Download</button>{' '}
                      {canBuild && <button className="gs-btn ghost sm" disabled={Boolean(busy)} onClick={() => remove(p.name)}>Delete</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {changes?.base && list.length === 0 && <div className="gs-muted" style={{ marginTop: 6 }}>No presets saved for {changes.base} yet.</div>}
        </div>
      </div>
    </div>
  );
};
