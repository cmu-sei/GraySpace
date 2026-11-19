/** Step 2: which enclaves the deployment builds, each with its prefix, and their specs (GET /enclaves, PUT on save). */
import React, { useState } from 'react';
import { Tip } from '../../components/Tip';
import { EnclaveEditor, newEnclave, validSlug } from './EnclaveEditor';
import { Findings } from './parts';
import { Builder } from './useBuilder';

const PREFIX = /^[a-z][a-z0-9]{0,7}$/;

export const EnclavesStep: React.FC<{ b: Builder; readOnly: boolean }> = ({ b, readOnly }) => {
  const [editing, setEditing] = useState<string | null>(b.enclaves[0]?.slug || b.library[0]?.slug || null);
  const [naming, setNaming] = useState<string | null>(null);
  const chosen = new Map(b.enclaves.map((e) => [e.slug, e]));
  const spec = editing ? b.specs[editing] : undefined;

  const create = () => {
    if (!naming || !validSlug(naming) || b.specs[naming]) return;
    const spec = newEnclave(naming, b.capabilities);
    b.updateSpec(spec);
    b.select(naming, true, spec.prefix);
    setEditing(naming);
    setNaming(null);
  };

  return (
    <div className="gs-grid2" style={{ gridTemplateColumns: 'minmax(340px, 1fr) minmax(0, 1.5fr)', alignItems: 'start' }}>
      <div className="gs-box">
        <h3>Enclave library <span className="gs-spacer" />{!readOnly && <button className="gs-btn ghost sm" onClick={() => setNaming('')}>＋ New enclave</button>}</h3>
        {naming !== null && (
          <div className="gs-row" style={{ marginBottom: 8 }}>
            <input className="gs-input gs-mono" autoFocus placeholder="slug, e.g. city-hospital" value={naming}
              onChange={(e) => setNaming(e.target.value.toLowerCase())} onKeyDown={(e) => e.key === 'Enter' && create()} aria-label="New enclave slug" />
            <button className="gs-btn sm" disabled={!validSlug(naming) || Boolean(b.specs[naming])} onClick={create}>Create</button>
            <button className="gs-btn ghost sm" onClick={() => setNaming(null)}>Cancel</button>
          </div>
        )}
        {b.library.length === 0 && <div className="gs-muted">No enclaves defined.</div>}
        {b.library.map((s) => {
          const entry = chosen.get(s.slug);
          const locked = b.readOnly(s.slug);
          return (
            <div key={s.slug} className={`gs-lib ${editing === s.slug ? 'editing' : ''}`}>
              <input type="checkbox" aria-label={`Build ${s.slug}`} disabled={readOnly} checked={Boolean(entry)} onChange={(e) => b.select(s.slug, e.target.checked)} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="gs-row">
                  <b>{s.slug}</b>
                  {s.owner === 'ghosts' && <span className="gs-pill warn">GHOSTS-owned</span>}
                  {s.instances.length > 0 && <span className="gs-pill info">{s.instances.join(', ')} live</span>}
                </div>
                <div className="gs-muted" style={{ fontSize: 11 }}>
                  {b.specs[s.slug]?.description || s.description}
                  {b.specs[s.slug] && ` · ${(b.specs[s.slug].sites || []).length} sites · ${(b.specs[s.slug].hosts || []).reduce((n, h) => n + (h.count || 1), 0)} hosts`}
                </div>
                {entry && <Findings validation={b.validations[s.slug]} quietWhenValid />}
              </div>
              <div className="gs-row" style={{ flex: 'none' }}>
                {entry && (
                  <label className="gs-row gs-muted" style={{ gap: 4, fontSize: 11 }}>
                    Prefix<Tip text="Starts every hostname in this enclave's instance; unique in this deployment" />
                    <input className="gs-input gs-mono" style={{ width: 70 }} aria-label={`${s.slug} prefix`}
                      disabled={readOnly} value={entry.prefix} onChange={(e) => b.setPrefix(s.slug, e.target.value.toLowerCase())} />
                  </label>
                )}
                <button className={`gs-btn sm ${editing === s.slug ? '' : 'ghost'}`} aria-pressed={editing === s.slug} onClick={() => setEditing(s.slug)}>
                  {editing === s.slug ? (locked ? 'Viewing' : 'Editing') : locked ? 'View' : 'Edit'}
                </button>
                {!readOnly && !locked && (
                  <button className="gs-btn ghost sm" aria-label={`Delete ${s.slug}`} disabled={s.instances.length > 0}
                    title={s.instances.length > 0 ? 'Destroy its instances first' : 'Delete the enclave'}
                    onClick={() => window.confirm(`Delete ${s.slug} and every version of it?`)
                      && b.remove(s.slug).then((gone) => gone && editing === s.slug && setEditing(null))}>Delete</button>
                )}
              </div>
            </div>
          );
        })}
        {b.enclaves.some((e) => !PREFIX.test(e.prefix)) && <div className="gs-finding bad">Prefixes are 1 to 8 lowercase letters and digits, starting with a letter.</div>}
      </div>
      <div className="gs-box">
        {spec ? (
          <>
            <h3>
              {b.readOnly(spec.slug) ? 'Viewing' : 'Editing'} <span className="gs-mono">{spec.slug}</span>
              {b.library.find((s) => s.slug === spec.slug)?.owner === 'ghosts' && <span className="gs-muted" style={{ fontWeight: 400 }}>GHOSTS owns it: change it in GHOSTS</span>}
            </h3>
            <EnclaveEditor key={spec.slug} spec={spec} caps={b.capabilities} readOnly={b.readOnly(spec.slug)} onChange={b.updateSpec} />
            <div style={{ marginTop: 8 }}><Findings validation={b.validations[spec.slug]} /></div>
          </>
        ) : <div className="gs-empty">Choose an enclave to see its spec, or start a new one.</div>}
      </div>
    </div>
  );
};
