/** Pieces the builder's steps share. */
import React from 'react';
import { Finding } from '../../types/slices';
import { Validation } from '../../types/build';

export const Findings: React.FC<{ validation?: Validation; quietWhenValid?: boolean }> = ({ validation, quietWhenValid }) => {
  if (!validation) return null;
  if (validation.checking && !validation.findings.length) return <div className="gs-muted" style={{ fontSize: 11 }}>Checking…</div>;
  if (!validation.findings.length) return quietWhenValid ? null : <div className="gs-finding ok">Valid</div>;
  return (
    <>
      {validation.findings.map((f: Finding, i) => (
        <div key={`${f.code}-${i}`} className={`gs-finding ${f.severity === 'error' ? 'bad' : ''}`}>
          <b className="gs-mono">{f.code}</b>{f.path ? <span className="gs-muted"> at {f.path}</span> : null} {f.message}
        </div>
      ))}
    </>
  );
};

export const Bar: React.FC<{ label: string; used: number | null | undefined; total: number | null | undefined; unit?: (n: number) => string }> = ({
  label, used, total, unit = (n) => `${(n / 2 ** 30).toFixed(0)} GiB`,
}) => {
  const pct = used != null && total ? Math.round((used / total) * 100) : null;
  const tone = pct == null ? '' : pct > 80 ? 'var(--gs-bad)' : pct > 60 ? 'var(--gs-warn)' : 'var(--gs-accent)';
  return (
    <div style={{ margin: '7px 0' }}>
      <div className="gs-row"><span>{label}</span><span className="gs-spacer" /><span className="gs-muted gs-mono">{pct == null ? 'unknown' : `${pct}%`}{used != null && total && unit(total) ? ` · ${unit(used)} of ${unit(total)}` : ''}</span></div>
      <div className="gs-bar"><i style={{ width: `${pct ?? 0}%`, background: tone }} /></div>
    </div>
  );
};

/** Comma-separated text, for lists the form edits in one field. */
export const toList = (text: string): string[] => text.split(',').map((s) => s.trim()).filter(Boolean);
