/**
 * Where a deployment runs and whether it has a Player view: a Proxmox connection and node, and a
 * Crucible connection, from the platform (Admin > Platform). Used by the new-deployment form and
 * a deployment's settings.
 */
import React from 'react';
import { DeploymentOptions, Placement, Platform } from '../types/control';

interface Props {
  platform: Platform | null;
  placement: Placement | null;
  options: DeploymentOptions;
  onChange: (placement: Placement | null, options: DeploymentOptions) => void;
  disabled?: boolean;
  idPrefix?: string;
}

/** The default connection and its default node: what a new deployment gets without choosing. */
export const defaultPlacement = (platform: Platform | null): Placement | null => {
  const conn = platform?.proxmox.find((c) => c.id === platform.default_proxmox) ?? platform?.proxmox[0];
  return conn ? { connection: conn.id, node: conn.default_node } : null;
};

export const PlacementPicker: React.FC<Props> = ({ platform, placement, options, onChange, disabled, idPrefix = 'pl' }) => {
  if (!platform) return <div className="gs-muted">Loading the platform…</div>;
  if (!platform.proxmox.length) {
    return <div className="gs-banner warn">There are no Proxmox connections yet: ask an admin to add one (Admin &gt; Platform).</div>;
  }
  const conn = platform.proxmox.find((c) => c.id === placement?.connection);
  const node = conn?.nodes.find((n) => n.name === (placement?.node || conn.default_node));
  const crucible = platform.crucible;
  // With one connection and one node there is nothing to choose
  const choice = platform.proxmox.length > 1 || (conn?.nodes.length ?? 0) > 1;
  return (
    <>
      {choice && (
        <div className="gs-grid2">
          <div className="gs-field">
            <label htmlFor={`${idPrefix}-connection`}>Proxmox connection</label>
            <select id={`${idPrefix}-connection`} disabled={disabled} value={placement?.connection ?? ''}
              onChange={(e) => {
                const next = platform.proxmox.find((c) => c.id === e.target.value);
                onChange(next ? { connection: next.id, node: next.default_node } : null, options);
              }}>
              {platform.proxmox.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.host})</option>)}
            </select>
          </div>
          <div className="gs-field">
            <label htmlFor={`${idPrefix}-node`}>Node</label>
            <select id={`${idPrefix}-node`} disabled={disabled || !conn} value={node?.name ?? ''}
              onChange={(e) => conn && onChange({ connection: conn.id, node: e.target.value }, options)}>
              {conn?.nodes.map((n) => <option key={n.name} value={n.name}>{n.name}</option>)}
            </select>
          </div>
        </div>
      )}
      <label className="gs-row" title={crucible.length ? undefined : 'An admin adds a Crucible connection in Admin > Platform'}>
        <input type="checkbox" disabled={disabled || !crucible.length} checked={Boolean(options.player)}
          onChange={(e) => onChange(placement, { ...options, player: e.target.checked, player_connection: e.target.checked ? options.player_connection ?? (crucible.length === 1 ? crucible[0].id : null) : null })} />
        Create a Crucible Player view {!crucible.length && <span className="gs-muted">(no Crucible connection yet)</span>}
      </label>
      {options.player && crucible.length > 1 && (
        <div className="gs-field" style={{ marginTop: 6 }}>
          <label htmlFor={`${idPrefix}-crucible`}>Crucible connection</label>
          <select id={`${idPrefix}-crucible`} disabled={disabled} value={options.player_connection ?? ''}
            onChange={(e) => onChange(placement, { ...options, player_connection: e.target.value || null })}>
            <option value="">Choose…</option>
            {crucible.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.player_api_url})</option>)}
          </select>
        </div>
      )}
    </>
  );
};
