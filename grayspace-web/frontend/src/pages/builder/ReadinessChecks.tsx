/**
 * The base's readiness checks (GET /base/preflight), one line per group: the worst status and, when
 * something isn't right, what. A line opens to its checks. The server's own checks show only when
 * one isn't ok; whoever can't change the platform is told to ask an admin.
 */
import React, { useState } from 'react';
import { useOptionalAuth } from '../../auth/AuthProvider';
import { Preflight, PreflightCheck } from '../../types/build';

const DOT: Record<string, string> = { ok: 'ok', warning: 'warn', error: 'bad', unchecked: '' };
const RANK: Record<string, number> = { ok: 0, unchecked: 1, warning: 2, error: 3 };

interface Group {
  key: string;
  label: string;
  names: string[];
  /** Shown only when one of its checks isn't ok */
  quiet?: boolean;
  /** An admin's to fix */
  platform?: boolean;
}

export const GROUPS: Group[] = [
  { key: 'base', label: 'Core version', names: ['version', 'instances'] },
  { key: 'proxmox', label: 'Proxmox', names: ['proxmox', 'storage', 'bridges', 'sdn', 'capacity', 'ssh', 'environment-id'], platform: true },
  { key: 'crucible', label: 'Crucible', names: ['crucible'], platform: true },
  { key: 'server', label: 'GraySpace server', names: [], quiet: true, platform: true },
];

const groupOf = (check: PreflightCheck): Group => GROUPS.find((g) => g.names.includes(check.name)) ?? GROUPS[GROUPS.length - 1];

export const worst = (checks: PreflightCheck[]): PreflightCheck['status'] =>
  checks.reduce<PreflightCheck['status']>((w, c) => (RANK[c.status] > RANK[w] ? c.status : w), 'ok');

export const ReadinessChecks: React.FC<{ preflight: Preflight }> = ({ preflight }) => {
  const admin = Boolean(useOptionalAuth()?.can('platform:manage'));
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setOpen((o) => {
    const next = new Set(o);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  return (
    <>
      {GROUPS.map((g) => {
        const checks = preflight.checks.filter((c) => groupOf(c) === g);
        const status = worst(checks);
        if (!checks.length || (g.quiet && status === 'ok')) return null;
        const first = checks.find((c) => c.status === status && status !== 'ok');
        const expanded = open.has(g.key);
        return (
          <div key={g.key} style={{ padding: '3px 0' }}>
            <button className="gs-row gs-plain" aria-expanded={expanded} onClick={() => toggle(g.key)} style={{ alignItems: 'flex-start', width: '100%', textAlign: 'left' }}>
              <span className="gs-muted" style={{ width: 10 }}>{expanded ? '▾' : '▸'}</span>
              <span className={`gs-dot ${DOT[status]}`} style={{ marginTop: 4 }} />
              <span><b>{g.label}</b> <span className="gs-muted">{first ? first.message : 'Ready'}</span></span>
            </button>
            {expanded && checks.map((c) => (
              <div key={c.name} className="gs-row" style={{ alignItems: 'flex-start', padding: '2px 0 2px 26px' }}>
                <span className={`gs-dot ${DOT[c.status]}`} style={{ marginTop: 4 }} />
                <span>{c.message}</span>
              </div>
            ))}
            {g.platform && status === 'error' && !admin && <div className="gs-muted" style={{ paddingLeft: 26, fontSize: 11 }}>Contact your administrator.</div>}
          </div>
        );
      })}
    </>
  );
};
