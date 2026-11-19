/**
 * Link traffic shaping panel component (applies symmetrically to both ends)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Link, TrafficConfig, LinkTrafficConfig, LinkTrafficRates } from '../types';
import { apiService } from '../services/api';
import { useTopologyStore } from '../stores/topologyStore';
import { getStateColor } from '../utils/colors';
import { formatBytesRate, formatBGPType } from '../utils/formatters';

interface LinkTrafficPanelProps {
  link: Link;
  onClose: () => void;
}

interface SliderConfig {
  label: string;
  key: keyof TrafficConfig;
  min: number;
  max: number;
  step: number;
  unit: string;
}

const SLIDER_CONFIGS: SliderConfig[] = [
  { label: 'Latency', key: 'latency_ms', min: 0, max: 1000, step: 10, unit: 'ms' },
  { label: 'Jitter', key: 'jitter_ms', min: 0, max: 500, step: 5, unit: 'ms' },
  { label: 'Bandwidth', key: 'bandwidth_mbps', min: 1, max: 1000, step: 1, unit: 'Mbps' },
  { label: 'Packet Loss', key: 'packet_loss_percent', min: 0, max: 50, step: 1, unit: '%' },
];

export const LinkTrafficPanel: React.FC<LinkTrafficPanelProps> = ({
  link,
  onClose,
}) => {
  const linkStates = useTopologyStore((state) => state.linkStates);
  const linkState = linkStates.get(link.id);

  const [linkConfig, setLinkConfig] = useState<LinkTrafficConfig | null>(null);
  const [trafficRates, setTrafficRates] = useState<LinkTrafficRates | null>(null);
  const [config, setConfig] = useState<TrafficConfig>({
    latency_ms: null,
    jitter_ms: null,
    bandwidth_mbps: null,
    packet_loss_percent: null,
  });
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadLinkConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [data, rates] = await Promise.all([
        apiService.getLinkTrafficConfig(link.id),
        apiService.getLinkTrafficRates(link.id).catch(() => null),
      ]);
      setLinkConfig(data);
      setTrafficRates(rates);
      if (data.config) {
        setConfig(data.config);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load link traffic config');
    } finally {
      setLoading(false);
    }
  }, [link.id]);

  useEffect(() => {
    loadLinkConfig();
  }, [loadLinkConfig]);

  const handleSliderChange = (key: keyof TrafficConfig, value: number) => {
    setConfig((prev) => ({
      ...prev,
      [key]: value === 0 && key !== 'bandwidth_mbps' ? null : value,
    }));
  };

  const handleApply = async () => {
    setApplying(true);
    setError(null);
    setSuccess(null);

    try {
      await apiService.applyLinkTrafficConfig(link.id, config);
      setSuccess('Traffic shaping applied to both ends of the link');
      await loadLinkConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to apply traffic config');
    } finally {
      setApplying(false);
    }
  };

  const handleClear = async () => {
    setApplying(true);
    setError(null);
    setSuccess(null);

    try {
      await apiService.clearLinkTrafficConfig(link.id);
      setConfig({
        latency_ms: null,
        jitter_ms: null,
        bandwidth_mbps: null,
        packet_loss_percent: null,
      });
      setSuccess('Traffic shaping cleared — baseline latency restored');
      await loadLinkConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clear traffic config');
    } finally {
      setApplying(false);
    }
  };

  const hasActiveConfig = (): boolean => {
    return !!(config.latency_ms || config.jitter_ms || config.bandwidth_mbps || config.packet_loss_percent);
  };

  return (
    <div className="link-traffic-panel">
      <div className="panel-header">
        <h3>{link.source_hostname} ↔ {link.target_hostname}</h3>
        <button className="close-button" onClick={onClose}>
          x
        </button>
      </div>

      <div className="panel-content">
        {loading ? (
          <div className="loading">Loading link configuration...</div>
        ) : error && !linkConfig ? (
          <div className="error">{error}</div>
        ) : (
          <>
            {/* Link Health */}
            <div className="info-section">
              <h4>Link Health</h4>
              <div style={{ marginBottom: '0.75rem' }}>
                <span
                  className="state-badge large"
                  style={{ backgroundColor: getStateColor(linkState?.state) }}
                >
                  {(linkState?.state || 'UNKNOWN').toUpperCase()}
                </span>
              </div>
              {linkState?.diagnostics && linkState.diagnostics.length > 0 && (
                <div className="diagnostics-list">
                  {linkState.diagnostics.map((diag, i) => (
                    <div key={i} className="diagnostic-item">
                      <span style={{ color: 'var(--gs-bad)', fontWeight: 600 }}>!</span> {diag}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Traffic Rates */}
            {trafficRates && (
              <div className="info-section">
                <h4>Traffic Rates</h4>
                <div className="traffic-rates-grid">
                  <div className="traffic-rate-endpoint">
                    <div className="traffic-rate-hostname">{trafficRates.source.hostname}</div>
                    <div className="traffic-rate-iface">{trafficRates.source.interface}</div>
                    <div className="traffic-rate-values">
                      <div className="traffic-rate-row">
                        <span className="traffic-rate-label">RX:</span>
                        <span className="traffic-rate-value">{formatBytesRate(trafficRates.source.rx_bytes_per_sec)}</span>
                      </div>
                      <div className="traffic-rate-row">
                        <span className="traffic-rate-label">TX:</span>
                        <span className="traffic-rate-value">{formatBytesRate(trafficRates.source.tx_bytes_per_sec)}</span>
                      </div>
                    </div>
                  </div>
                  <div className="traffic-rate-endpoint">
                    <div className="traffic-rate-hostname">{trafficRates.target.hostname}</div>
                    <div className="traffic-rate-iface">{trafficRates.target.interface}</div>
                    <div className="traffic-rate-values">
                      <div className="traffic-rate-row">
                        <span className="traffic-rate-label">RX:</span>
                        <span className="traffic-rate-value">{formatBytesRate(trafficRates.target.rx_bytes_per_sec)}</span>
                      </div>
                      <div className="traffic-rate-row">
                        <span className="traffic-rate-label">TX:</span>
                        <span className="traffic-rate-value">{formatBytesRate(trafficRates.target.tx_bytes_per_sec)}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Link Information */}
            <div className="info-section">
              <h4>Link Details</h4>
              <div className="info-grid">
                <span className="info-label">Source:</span>
                <span className="info-value">
                  {link.source_hostname}
                  {linkConfig?.source_interface && (
                    <span className="interface-badge">{linkConfig.source_interface}</span>
                  )}
                </span>

                <span className="info-label">Target:</span>
                <span className="info-value">
                  {link.target_hostname}
                  {linkConfig?.target_interface && (
                    <span className="interface-badge">{linkConfig.target_interface}</span>
                  )}
                </span>

                <span className="info-label">Type:</span>
                <span className="info-value">{formatBGPType(link.type)}</span>

                <span className="info-label">Subnet:</span>
                <span className="info-value">{link.subnet || 'N/A'}</span>
              </div>
            </div>

            {/* Traffic Shaping Sliders */}
            <div className="sliders-section">
              <h4>Traffic Shaping Configuration</h4>
              <div className="symmetry-note">
                Values represent the total link-level effect. Latency, jitter, and
                packet loss are split evenly between both ends (e.g., 500ms latency
                adds 250ms at each interface). Bandwidth applies independently per
                direction.
              </div>

              {SLIDER_CONFIGS.map(({ label, key, min, max, step, unit }) => (
                <div key={key} className="slider-row">
                  <label>
                    {label}: {config[key] ?? (key === 'bandwidth_mbps' ? max : 0)} {unit}
                  </label>
                  <input
                    type="range"
                    min={min}
                    max={max}
                    step={step}
                    value={config[key] ?? (key === 'bandwidth_mbps' ? max : 0)}
                    onChange={(e) =>
                      handleSliderChange(key, parseFloat(e.target.value))
                    }
                    disabled={applying}
                  />
                </div>
              ))}

              {/* Action Buttons */}
              <div className="action-buttons">
                <button
                  className="apply-button"
                  onClick={handleApply}
                  disabled={applying}
                >
                  {applying ? 'Applying...' : 'Apply'}
                </button>
                <button
                  className="clear-button"
                  onClick={handleClear}
                  disabled={applying || !hasActiveConfig()}
                >
                  Clear Rules
                </button>
              </div>

              {/* Status Messages */}
              {error && <div className="error-message">{error}</div>}
              {success && <div className="success-message">{success}</div>}
            </div>
          </>
        )}
      </div>
    </div>
  );
};
