/**
 * One job: what it does stage by stage (the stage titles from the API), a rollout's jobs, the log
 * as it streams, and Cancel and Retry for those who build here.
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { CopyButton } from '../../components/CopyButton';
import { apiService } from '../../services/api';
import { JobRecord } from '../../types/slices';
import { ago, duration, isFinished, kindLabel, progressShare, progressText, stageStatuses, stageTitle, STATE_TONE, stripAnsi } from './format';

const MARK: Record<string, string> = { done: '✓', current: '⟳', failed: '✕', skipped: '↷', pending: '○' };

export const StatePill: React.FC<{ state: string }> = ({ state }) => (
  <span className={`gs-pill ${STATE_TONE[state] ?? ''}`}>{state === 'running' ? '⟳ ' : ''}{state}</span>
);

export const StageList: React.FC<{ job: JobRecord }> = ({ job }) => {
  const statuses = stageStatuses(job);
  return (
    <ol className="gs-stages">
      {job.stages.map((stage, i) => (
        <li key={stage} className={statuses[i]} title={stage}>
          <span className="gs-stage-mark">{MARK[statuses[i]]}</span>
          <span>{stageTitle(job, stage)}</span>
          {statuses[i] === 'current' && job.attempt && <span className="gs-muted">· {job.attempt}</span>}
        </li>
      ))}
    </ol>
  );
};

/** A rollout's jobs, each with where it is, linking to its detail. */
const Children: React.FC<{ deploymentId: string; job: JobRecord }> = ({ deploymentId, job }) => {
  const [children, setChildren] = useState<JobRecord[]>([]);
  useEffect(() => {
    let stopped = false;
    const load = () => apiService.getJobs({ parent: job.id, limit: 200 }).then((list) => !stopped && setChildren(list.reverse())).catch(() => undefined);
    load();
    const timer = isFinished(job) ? undefined : setInterval(load, 2000);
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
    };
  }, [job]);
  const planned = job.stages;
  // Stages the rollout runs itself (presets, the Player view) have no child job: their state is the rollout's
  const own = stageStatuses(job);
  const OWN: Record<string, string> = { done: 'succeeded', skipped: 'succeeded', current: 'running', failed: 'failed', pending: 'waiting' };
  const inline = (stage: string) => stage === 'player' || stage === 'preset' || stage.startsWith('preset:');
  const byStage = (stage: string) => {
    const ids = ((job.result as { children?: Record<string, string> } | null)?.children) || {};
    return children.find((c) => c.id === ids[stage]);
  };
  return (
    <div className="gs-box">
      <h3>Steps</h3>
      {planned.map((stage, i) => {
        const child = byStage(stage);
        return (
          <div key={stage} className={`gs-child ${child?.state ?? 'queued'}`}>
            <div className="gs-row" title={stage}>
              <span>{stageTitle(job, stage)}</span>
              <span className="gs-spacer" />
              {child ? <StatePill state={child.state} />
                : inline(stage) && OWN[own[i]] !== 'waiting' ? <StatePill state={OWN[own[i]]} /> : <span className="gs-pill">waiting</span>}
              {child && <Link className="gs-btn ghost sm" to={`/d/${deploymentId}/jobs/${child.id}`}>Open</Link>}
            </div>
            {child && child.state !== 'succeeded' && <div className="gs-muted" style={{ marginTop: 4, fontSize: 11 }}>{progressText(child)}</div>}
            {child?.error && <div className="gs-muted" style={{ marginTop: 4 }}>{child.error}</div>}
          </div>
        );
      })}
    </div>
  );
};

/** A failed job's details as text, for whoever looks into it: the job, where it failed, the error and the log's end. */
const failureReport = (deploymentId: string, job: JobRecord, log: string): string => [
  `Deployment: ${deploymentId}`,
  `Job: ${job.id} (${kindLabel(job.kind)}, ${job.target})`,
  `State: ${progressText(job)}`,
  `Error: ${job.error ?? ''}`,
  '',
  'Log (last 60 lines):',
  ...stripAnsi(log).split('\n').slice(-60),
].join('\n');

/**
 * summary: where the job is in one line with a progress bar, its error, and the steps and log behind
 * Details (Review's deploy); otherwise all of it.
 */
export const JobDetail: React.FC<{ deploymentId: string; jobId: string; canBuild: boolean; onRetried: (id: string) => void; summary?: boolean }> = ({
  deploymentId, jobId, canBuild, onRetried, summary = false,
}) => {
  const [job, setJob] = useState<JobRecord | null>(null);
  const [log, setLog] = useState('');
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);
  // Follow the log's end until the reader scrolls up, and again once they scroll back down. Decided
  // from their scrolling, not after a chunk lands: a chunk of Terraform or Ansible output is often
  // taller than any "near the end" margin.
  const following = useRef(true);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setJob(null);
    setError(null);
    const poll = async () => {
      try {
        const current = await apiService.getJob(jobId);
        if (stopped) return;
        setJob(current);
        if (isFinished(current)) return;
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : String(err));
      }
      if (!stopped) timer = setTimeout(poll, 2000);
    };
    poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  useEffect(() => {
    const controller = new AbortController();
    setLog('');
    following.current = true;
    apiService.streamJobLog(jobId, (text) => setLog((prev) => prev + text), controller.signal).catch(() => undefined);
    return () => controller.abort();
  }, [jobId]);

  useLayoutEffect(() => {
    const el = logRef.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [log]);
  const onLogScroll = () => {
    const el = logRef.current;
    if (el) following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };

  const act = async (fn: () => Promise<void>) => {
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!job) return <div className="gs-box">{error ? <div className="gs-banner bad">{error}</div> : <span className="gs-muted">Loading {jobId}…</span>}</div>;
  const finished = isFinished(job);
  const retryable = (job.state === 'failed' || job.state === 'cancelled') && !job.parent;

  return (
    <div>
      <div className="gs-box">
        <h3>
          {kindLabel(job.kind)} <span className="gs-tag">{job.target}</span> <StatePill state={job.state} />
          <span className="gs-spacer" />
          {canBuild && !finished && !job.cancel_requested && (
            <button className="gs-btn ghost sm" onClick={() => act(async () => setJob(await apiService.cancelJob(job.id)))}>Cancel</button>
          )}
          {canBuild && retryable && (
            <button className="gs-btn sm" onClick={() => act(async () => onRetried((await apiService.retryJob(job.id)).job.id))}>
              Retry{job.stage ? ` from ${job.stage}` : ''}
            </button>
          )}
        </h3>
        <div className="gs-muted">
          <span className="gs-mono">{job.id}</span>
          {job.by && <> · by {job.by}</>}
          {' · '}queued {ago(job.created)}
          {job.started && <> · {finished ? 'took' : 'running for'} {duration(job)}</>}
          {job.retry_of && <> · retry of <Link to={`/d/${deploymentId}/jobs/${job.retry_of}`}>{job.retry_of}</Link></>}
        </div>
        {job.parent && (
          <div className="gs-banner info" style={{ marginTop: 8, marginBottom: 0 }}>
            Part of deploy <Link to={`/d/${deploymentId}/jobs/${job.parent}`}>{job.parent}</Link>{job.state === 'failed' ? '; retry the deploy to resume it' : ''}.
          </div>
        )}
        {job.cancel_requested && !finished && <div className="gs-banner warn" style={{ marginTop: 8, marginBottom: 0 }}>Cancelling: interrupting the running command.</div>}
        {summary && (
          <div style={{ marginTop: 8 }}>
            <div>{progressText(job)}</div>
            <div className="gs-bar" style={{ marginTop: 4 }}><i style={{ width: `${Math.round(progressShare(job) * 100)}%`, background: job.state === 'failed' ? 'var(--gs-bad)' : undefined }} /></div>
          </div>
        )}
        {job.error && (
          <div className="gs-banner bad" style={{ marginTop: 8, marginBottom: 0 }}>
            {job.error}
            {(job.state === 'failed' || job.state === 'cancelled') && (
              <div style={{ marginTop: 6 }}>
                <CopyButton text={() => failureReport(deploymentId, job, log)} label="Copy debug info" />
              </div>
            )}
          </div>
        )}
        {error && <div className="gs-banner bad" style={{ marginTop: 8, marginBottom: 0 }}>{error}</div>}
      </div>
      {summary ? (
        <details className="gs-box">
          <summary style={{ cursor: 'pointer' }}>Details</summary>
          {job.kind === 'rollout' ? <Children deploymentId={deploymentId} job={job} /> : <StageList job={job} />}
          <pre ref={logRef} className="gs-log" onScroll={onLogScroll}>{stripAnsi(log) || (finished ? 'No output.' : 'Waiting for output…')}</pre>
        </details>
      ) : (
        <>
          {job.kind === 'rollout' ? <Children deploymentId={deploymentId} job={job} /> : <div className="gs-box"><h3>Stages</h3><StageList job={job} /></div>}
          <div className="gs-box">
            <h3>Log</h3>
            <pre ref={logRef} className="gs-log" onScroll={onLogScroll}>{stripAnsi(log) || (finished ? 'No output.' : 'Waiting for output…')}</pre>
          </div>
        </>
      )}
    </div>
  );
};
