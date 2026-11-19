/**
 * A Proxmox node's headroom (GET /api/v1/nodes), on Platform: each node chip is colored by how full
 * it is (memory or storage, whichever is fuller) and opens its capacity, deployments and lock.
 */
import React from 'react';
import { NodeInfo, ProxmoxConnection } from '../../types/control';
import { Bar } from '../builder/parts';

const GIB = 2 ** 30;

/** The fuller of memory and storage, 0 to 1; null when the node didn't say. */
export const fullness = (n: NodeInfo | undefined): number | null => {
  const c = n?.capacity;
  if (!c) return null;
  const shares = [
    c.memory_total && c.memory_free != null ? 1 - c.memory_free / c.memory_total : null,
    c.storage_total && c.storage_free != null ? 1 - c.storage_free / c.storage_total : null,
  ].filter((x): x is number => x != null);
  return shares.length ? Math.max(...shares) : null;
};

export const tone = (share: number | null): string => (share == null ? '' : share > 0.9 ? 'bad' : share > 0.75 ? 'warn' : 'ok');

export const summary = (n: NodeInfo | undefined): string => {
  const c = n?.capacity;
  if (!c) return 'Capacity unknown';
  const part = (label: string, total?: number | null, free?: number | null) =>
    total && free != null ? `${label} ${Math.round((1 - free / total) * 100)}% used (${((total - free) / GIB).toFixed(0)} of ${(total / GIB).toFixed(0)} GiB)` : null;
  return [part('Memory', c.memory_total, c.memory_free), part('Storage', c.storage_total, c.storage_free)].filter(Boolean).join(' · ') || 'Capacity unknown';
};

/** The node of a connection in GET /nodes: by connection, or by host for nodes listed without one. */
export const nodeOf = (nodes: NodeInfo[], c: ProxmoxConnection, name: string): NodeInfo | undefined =>
  nodes.find((n) => n.node === name && (n.connection ? n.connection === c.id : n.host === c.host));

export const NodeCard: React.FC<{ n: NodeInfo }> = ({ n }) => {
  const c = n.capacity;
  return (
    <>
      {c ? (
        <>
          <Bar label={`CPU (${c.cpus ?? '?'} cores)`} used={c.cpu_usage} total={1} unit={() => ''} />
          <Bar label="Memory" used={c.memory_total != null && c.memory_free != null ? c.memory_total - c.memory_free : null} total={c.memory_total} />
          <Bar label={`Storage${c.storage ? ` (${c.storage})` : ''}`} used={c.storage_total != null && c.storage_free != null ? c.storage_total - c.storage_free : null} total={c.storage_total} />
        </>
      ) : <div className="gs-muted">The node didn't answer.</div>}
      <div className="gs-row" style={{ flexWrap: 'wrap', gap: 5, marginTop: 8 }}>
        <span className="gs-muted">Deployments</span>{n.deployments.length ? n.deployments.map((d) => <span key={d} className="gs-tag">{d}</span>) : <span className="gs-muted">none</span>}
      </div>
      <div className="gs-muted" style={{ marginTop: 6 }}>
        Lock: {n.locked ? <span className="gs-pill info">held{n.lock_holder ? ` by ${n.lock_holder}'s job` : ''}</span> : 'free'}
      </div>
    </>
  );
};

export const NodeModal: React.FC<{ name: string; connection: string; n: NodeInfo | undefined; onClose: () => void }> = ({ name, connection, n, onClose }) => (
  <div className="gs-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
    <div className="gs-modal" role="dialog" aria-label={`Node ${name}`}>
      <h3><span className="gs-mono">{name}</span> <span className="gs-muted" style={{ fontWeight: 400 }}>{connection}</span></h3>
      {n ? <NodeCard n={n} /> : <div className="gs-muted">Not listed by the nodes' report yet.</div>}
      <div className="gs-actions"><button className="gs-btn ghost" onClick={onClose}>Close</button></div>
    </div>
  </div>
);
