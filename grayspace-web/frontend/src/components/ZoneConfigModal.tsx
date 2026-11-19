/**
 * Zone configuration modal for customer interfaces
 */
import React, { useState, useEffect } from 'react';
import { CustomerInterfaceInfo, ZoneInfo } from '../types';
import { apiService } from '../services/api';

interface ZoneConfigModalProps {
  hostname: string;
  interfaceInfo: CustomerInterfaceInfo;
  onClose: () => void;
}

export const ZoneConfigModal: React.FC<ZoneConfigModalProps> = ({
  hostname,
  interfaceInfo,
  onClose,
}) => {
  const [zones, setZones] = useState<ZoneInfo[]>([]);
  const [selectedZone, setSelectedZone] = useState<string | null>(interfaceInfo.zone);
  const [vlanTag, setVlanTag] = useState<string>(interfaceInfo.vlan_tag?.toString() || '');
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  useEffect(() => {
    const loadZones = async () => {
      setLoading(true);
      try {
        const zonesData = await apiService.getZones();
        setZones(zonesData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load zones');
      } finally {
        setLoading(false);
      }
    };

    loadZones();
  }, []);

  const isVlanZoneSelected = !!(
    selectedZone && zones.find((z) => z.zone_id === selectedZone)?.type === 'vlan'
  );

  const handleApply = async () => {
    if (!selectedZone) {
      setError('Please select a zone');
      return;
    }

    if (selectedZone === interfaceInfo.zone) {
      setError('Selected zone is the same as current zone');
      return;
    }

    // Validate VLAN tag if moving to VLAN zone
    let parsedVlanTag: number | undefined;
    if (isVlanZoneSelected) {
      const tagNum = parseInt(vlanTag, 10);
      if (!vlanTag || isNaN(tagNum) || tagNum < 1 || tagNum > 4094) {
        setError('Please enter a valid VLAN tag (1-4094)');
        return;
      }
      parsedVlanTag = tagNum;
    }

    setApplying(true);
    setError(null);
    setSuccess(null);
    setWarnings([]);

    try {
      const result = await apiService.changeInterfaceZone(
        hostname,
        interfaceInfo.interface_name,
        selectedZone,
        parsedVlanTag
      );

      if (result.success) {
        setSuccess(result.message);
        setWarnings(result.warnings || []);
        // Close modal after short delay on success
        setTimeout(() => onClose(), 1500);
      } else {
        setError(result.message);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to change zone');
    } finally {
      setApplying(false);
    }
  };

  const zoneTypeLabel = (type: string | null | undefined, vlan?: number | null): string =>
    type === 'simple' ? 'Isolated' : type === 'vlan' ? (vlan ? `External, VLAN ${vlan}` : 'External (VLAN)') : type || '';
  const changing = isVlanZoneSelected && selectedZone !== interfaceInfo.zone;

  return (
    <div className="gs-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && !applying && onClose()}>
      <div className="gs-modal" role="dialog" aria-label="Configure zone">
        <h3>Configure zone <span className="gs-tag">{hostname}</span></h3>
        {loading ? (
          <div className="gs-muted">Loading zones…</div>
        ) : (
          <>
            <dl className="gs-kv">
              <dt>Interface</dt>
              <dd className="gs-mono">{interfaceInfo.interface_name}</dd>
              <dt>Network</dt>
              <dd className="gs-mono">{interfaceInfo.customer_net || '—'}</dd>
              <dt>Current zone</dt>
              <dd>
                <span className="gs-mono">{interfaceInfo.zone || 'none'}</span>
                {interfaceInfo.zone_type && (
                  <> <span className={`gs-pill ${interfaceInfo.zone_type === 'vlan' ? 'warn' : 'ok'}`}>
                    {zoneTypeLabel(interfaceInfo.zone_type, interfaceInfo.vlan_tag)}
                  </span></>
                )}
              </dd>
            </dl>

            <div className="gs-field"><label>New zone</label></div>
            {zones.map((zone) => (
              <div
                key={zone.zone_id}
                className={`gs-choice${selectedZone === zone.zone_id ? ' on' : ''}`}
                role="radio"
                aria-checked={selectedZone === zone.zone_id}
                onClick={() => {
                  setSelectedZone(zone.zone_id);
                  setError(null);
                }}
              >
                <div className="gs-row">
                  <b className="gs-mono">{zone.zone_id}</b>
                  <span className={`gs-pill ${zone.type === 'vlan' ? 'warn' : 'ok'}`}>{zoneTypeLabel(zone.type)}</span>
                  <span className="gs-spacer" />
                  {zone.zone_id === interfaceInfo.zone && <span className="gs-pill info">Current</span>}
                </div>
                {zone.description && <div className="gs-muted" style={{ marginTop: 3 }}>{zone.description}</div>}
              </div>
            ))}

            {changing && (
              <>
                <div className="gs-field" style={{ marginTop: 10 }}>
                  <label htmlFor="vlan-tag">VLAN tag (1–4094)</label>
                  <input
                    id="vlan-tag"
                    type="number"
                    min="1"
                    max="4094"
                    value={vlanTag}
                    onChange={(e) => setVlanTag(e.target.value)}
                    placeholder="e.g. 210"
                    disabled={applying}
                  />
                </div>
                <div className="gs-banner warn">
                  <span>A VLAN zone exposes this customer network to external traffic. The tag must match your physical network.</span>
                </div>
              </>
            )}

            {error && <div className="gs-banner bad">{error}</div>}
            {success && <div className="gs-banner info">{success}</div>}
            {warnings.map((warning, i) => (
              <div key={i} className="gs-banner warn">{warning}</div>
            ))}
          </>
        )}

        <div className="gs-actions">
          <button className="gs-btn ghost" onClick={onClose} disabled={applying}>
            Cancel
          </button>
          <button
            className="gs-btn"
            onClick={handleApply}
            disabled={applying || loading || !selectedZone || selectedZone === interfaceInfo.zone || (changing && !vlanTag)}
          >
            {applying ? 'Applying…' : 'Apply'}
          </button>
        </div>
      </div>
    </div>
  );
};
