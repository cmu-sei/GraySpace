/**
 * Every job on the deployment (GET /api/v1/jobs), newest first: base jobs, rollouts and the jobs
 * they ran, instance creates and destroys, plans. The selected one is /d/:dep/jobs/:job.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { TERRAIN_EVENT } from '../../hooks/useTerrainEvents';
import { apiService } from '../../services/api';
import { JobRecord } from '../../types/slices';
import { ago, kindLabel, KIND_LABELS, stageTitle } from './format';
import { JobDetail, StatePill } from './JobDetail';

const STATES: Record<string, string[] | undefined> = { all: undefined, active: ['queued', 'running'], failed: ['failed', 'cancelled'] };

export const JobsPage: React.FC<{ deploymentId: string; jobId?: string; canBuild: boolean }> = ({ deploymentId, jobId, canBuild }) => {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<JobRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [show, setShow] = useState<keyof typeof STATES>('all');
  const [kind, setKind] = useState('');

  const load = useCallback(async () => {
    try {
      setJobs(await apiService.getJobs({ state: STATES[show], kind: kind ? [kind] : undefined, limit: 200 }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [show, kind]);

  useEffect(() => {
    load();
    const onEvent = (e: Event) => {
      const type = (e as CustomEvent).detail?.type;
      if (type === 'job.update') load();
    };
    window.addEventListener(TERRAIN_EVENT, onEvent);
    const timer = setInterval(load, 10000);
    return () => {
      window.removeEventListener(TERRAIN_EVENT, onEvent);
      clearInterval(timer);
    };
  }, [load]);

  const selected = jobId || jobs?.[0]?.id;
  // A rollout's jobs go under it, folded unless one is selected or opened
  const [unfolded, setUnfolded] = useState<Set<string>>(new Set());
  const ids = new Set((jobs || []).map((j) => j.id));
  const childrenOf = (id: string) => (jobs || []).filter((j) => j.parent === id).reverse();
  const tops = (jobs || []).filter((j) => !j.parent || !ids.has(j.parent));
  const selectedParent = jobs?.find((j) => j.id === selected)?.parent;
  const toggle = (id: string) => setUnfolded((u) => {
    const next = new Set(u);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const open = (id: string) => navigate(`/d/${deploymentId}/jobs/${id}`);

  return (
    <div className="gs-page gs-dark">
      <div className="gs-row" style={{ marginBottom: 10 }}>
        <div>
          <h2 className="gs-title">Jobs</h2>
        </div>
        <span className="gs-spacer" />
        <select className="gs-input" style={{ width: 170 }} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
          <option value="">All kinds</option>
          {Object.entries(KIND_LABELS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
        <div className="gs-seg">
          {(['all', 'active', 'failed'] as const).map((s) => (
            <button key={s} className={show === s ? 'on' : ''} onClick={() => setShow(s)}>{s === 'all' ? 'All' : s === 'active' ? 'Running' : 'Failed'}</button>
          ))}
        </div>
      </div>
      {error && <div className="gs-banner bad">{error}</div>}
      <div className="gs-split">
        <div className="gs-box" style={{ padding: 0, overflow: 'auto' }}>
          {jobs && jobs.length === 0 && <div className="gs-empty">No jobs{show !== 'all' || kind ? ' match' : ' yet'}.</div>}
          {jobs && jobs.length > 0 && (
            <table className="gs-table gs-clickable">
              <thead><tr><th>Job</th><th>Target</th><th>State</th><th>By</th><th>Queued</th></tr></thead>
              <tbody>
                {tops.flatMap((top) => {
                  const kids = childrenOf(top.id);
                  const shown = unfolded.has(top.id) || selectedParent === top.id;
                  return [top, ...(shown ? kids : [])].map((j) => (
                    <tr key={j.id} className={j.id === selected ? 'selected' : ''} onClick={() => open(j.id)}>
                      <td style={j.parent ? { paddingLeft: 24 } : undefined}>
                        {j === top && kids.length > 0 && (
                          <button className="gs-plain gs-muted" aria-label={`${shown ? 'Fold' : 'Unfold'} ${j.id}`} aria-expanded={shown}
                            onClick={(e) => { e.stopPropagation(); toggle(j.id); }} style={{ width: 14 }}>{shown ? '▾' : '▸'}</button>
                        )}
                        {kindLabel(j.kind)}<div className="gs-muted gs-mono" style={{ fontSize: 10.5 }}>{j.id}</div>
                      </td>
                      <td><span className="gs-tag">{j.target}</span></td>
                      <td><StatePill state={j.state} />{j.state === 'running' && j.stage && <div className="gs-muted" style={{ fontSize: 10.5 }}>{stageTitle(j, j.stage)}</div>}</td>
                      <td className="gs-muted">{j.by || 'service'}</td>
                      <td className="gs-muted">{ago(j.created)}</td>
                    </tr>
                  ));
                })}
              </tbody>
            </table>
          )}
          {!jobs && !error && <div className="gs-empty">Loading…</div>}
        </div>
        <div>{selected ? <JobDetail deploymentId={deploymentId} jobId={selected} canBuild={canBuild} onRetried={(id) => { load(); open(id); }} /> : null}</div>
      </div>
    </div>
  );
};
