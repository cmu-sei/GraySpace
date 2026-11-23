/**
 * Interfaces tab component showing backbone and customer interfaces
 */
import React, { useState, useEffect, useCallback } from 'react';
import { useTopologyStore } from '../stores/topologyStore';
import { CustomerInterfaceInfo, RouterLiveState, InterfaceTrafficStats } from '../types';
import { apiService } from '../services/api';
import { formatBGPType, formatBytes, formatBytesRate } from '../utils/formatters';
import { getStateColor } from '../utils/colors';
import { ZoneConfigModal } from './ZoneConfigModal';

interface InterfacesTabProps {
  hostname: string;
}

interface BackboneInterface {
  interfaceName: string;
  ip: string;
  peerHostname: string;
  peerInterface?: string;
  linkType: string;
  state: string;
}

export const InterfacesTab: React.FC<InterfacesTabProps> = ({ hostname }) => {
  const { links } = useTopologyStore();
  const [customerInterfaces, setCustomerInterfaces] = useState<CustomerInterfaceInfo[]>([]);
  const [routerState, setRouterState] = useState<RouterLiveState | null>(null);
  const [interfaceStatsMap, setInterfaceStatsMap] = useState<Map<string, InterfaceTrafficStats>>(new Map());
  const [expandedStats, setExpandedStats] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCustomerInterface, setSelectedCustomerInterface] = useState<CustomerInterfaceInfo | null>(null);

  // The interfaces gate the tab; the live state and the traffic stats (Prometheus) fill in as they arrive
  const load = useCallback((current: () => boolean, failure: string) => {
    setLoading(true);
    setError(null);
    apiService.getCustomerInterfaces(hostname)
      .then((interfaces) => { if (current()) setCustomerInterfaces(interfaces); })
      .catch((err) => { if (current()) setError(err instanceof Error ? err.message : failure); })
      .finally(() => { if (current()) setLoading(false); });
    apiService.getRouterState(hostname)
      .then((state) => { if (current()) setRouterState(state); })
      .catch(() => { if (current()) setRouterState(null); });
    apiService.getRouterInterfaceStats(hostname)
      .then((stats) => {
        if (!current()) return;
        const map = new Map<string, InterfaceTrafficStats>();
        for (const s of stats.interfaces) {
          map.set(s.interface, s);
        }
        setInterfaceStatsMap(map);
      })
      .catch(() => {});
  }, [hostname]);

  useEffect(() => {
    let current = true;
    load(() => current, 'Failed to load interfaces');
    return () => { current = false; };
  }, [load]);

  // Build interface state map from router live state
  const interfaceStateMap = new Map<string, string>();
  if (routerState) {
    for (const iface of routerState.interfaces) {
      interfaceStateMap.set(iface.interface, iface.state);
    }
  }

  // Build backbone interfaces from links
  const backboneInterfaces: BackboneInterface[] = links
    .filter((link) => link.source_hostname === hostname || link.target_hostname === hostname)
    .map((link) => {
      const isSource = link.source_hostname === hostname;
      const ifaceName = isSource
        ? (link.source_interface || 'eth?')
        : (link.target_interface || 'eth?');

      // Use actual interface state from Prometheus, fallback to unknown
      const ifaceState = interfaceStateMap.get(ifaceName) || 'unknown';

      return {
        interfaceName: ifaceName,
        ip: isSource ? link.source_ip : link.target_ip,
        peerHostname: isSource ? link.target_hostname : link.source_hostname,
        linkType: link.type,
        state: ifaceState,
      };
    });

  const handleRefresh = () => load(() => true, 'Failed to refresh');

  const handleZoneConfigClose = () => {
    setSelectedCustomerInterface(null);
    handleRefresh();
  };

  const toggleStats = (ifaceName: string) => {
    setExpandedStats((prev) => {
      const next = new Set(prev);
      if (next.has(ifaceName)) {
        next.delete(ifaceName);
      } else {
        next.add(ifaceName);
      }
      return next;
    });
  };

  const renderInterfaceStats = (ifaceName: string) => {
    const stats = interfaceStatsMap.get(ifaceName);
    const isExpanded = expandedStats.has(ifaceName);

    return (
      <div className="interface-stats-section">
        <button
          className="stats-toggle-button"
          onClick={() => toggleStats(ifaceName)}
        >
          {isExpanded ? '- Hide Stats' : '+ Show Stats'}
        </button>
        {isExpanded && stats && (
          <div className="interface-stats-grid">
            <div className="stats-row">
              <span className="stats-label">RX Rate:</span>
              <span className="stats-value">{formatBytesRate(stats.rx_bytes_rate)}</span>
            </div>
            <div className="stats-row">
              <span className="stats-label">TX Rate:</span>
              <span className="stats-value">{formatBytesRate(stats.tx_bytes_rate)}</span>
            </div>
            <div className="stats-row">
              <span className="stats-label">RX Total:</span>
              <span className="stats-value">{formatBytes(stats.rx_bytes_total)}</span>
            </div>
            <div className="stats-row">
              <span className="stats-label">TX Total:</span>
              <span className="stats-value">{formatBytes(stats.tx_bytes_total)}</span>
            </div>
            <div className="stats-row">
              <span className="stats-label">RX Packets:</span>
              <span className="stats-value">{stats.rx_packets_total != null ? Math.round(stats.rx_packets_total).toLocaleString() : 'N/A'}</span>
            </div>
            <div className="stats-row">
              <span className="stats-label">TX Packets:</span>
              <span className="stats-value">{stats.tx_packets_total != null ? Math.round(stats.tx_packets_total).toLocaleString() : 'N/A'}</span>
            </div>
            <div className="stats-row">
              <span className="stats-label">RX Errors:</span>
              <span className={`stats-value${stats.rx_errs_total && stats.rx_errs_total > 0 ? ' stats-error' : ''}`}>
                {stats.rx_errs_total != null ? Math.round(stats.rx_errs_total).toLocaleString() : 'N/A'}
              </span>
            </div>
            <div className="stats-row">
              <span className="stats-label">TX Errors:</span>
              <span className={`stats-value${stats.tx_errs_total && stats.tx_errs_total > 0 ? ' stats-error' : ''}`}>
                {stats.tx_errs_total != null ? Math.round(stats.tx_errs_total).toLocaleString() : 'N/A'}
              </span>
            </div>
            <div className="stats-row">
              <span className="stats-label">RX Drops:</span>
              <span className={`stats-value${stats.rx_drop_total && stats.rx_drop_total > 0 ? ' stats-warning' : ''}`}>
                {stats.rx_drop_total != null ? Math.round(stats.rx_drop_total).toLocaleString() : 'N/A'}
              </span>
            </div>
            <div className="stats-row">
              <span className="stats-label">TX Drops:</span>
              <span className={`stats-value${stats.tx_drop_total && stats.tx_drop_total > 0 ? ' stats-warning' : ''}`}>
                {stats.tx_drop_total != null ? Math.round(stats.tx_drop_total).toLocaleString() : 'N/A'}
              </span>
            </div>
            {stats.speed_bytes != null && (
              <div className="stats-row">
                <span className="stats-label">Link Speed:</span>
                <span className="stats-value">{formatBytesRate(stats.speed_bytes)}</span>
              </div>
            )}
          </div>
        )}
        {isExpanded && !stats && (
          <div className="interface-stats-grid">
            <div className="stats-row">
              <span className="stats-label">No stats available</span>
            </div>
          </div>
        )}
      </div>
    );
  };

  if (loading) {
    return <div className="panel-content"><div className="loading">Loading interfaces...</div></div>;
  }

  if (error && backboneInterfaces.length === 0 && customerInterfaces.length === 0) {
    return <div className="panel-content"><div className="error-message">{error}</div></div>;
  }

  return (
    <div className="panel-content interfaces-tab">
      {/* Backbone Interfaces Section */}
      <div className="info-section">
        <h3>Backbone Interfaces ({backboneInterfaces.length})</h3>
        {backboneInterfaces.length === 0 ? (
          <div className="no-interfaces-message">
            <p>No backbone interfaces found.</p>
          </div>
        ) : (
          <div className="interfaces-list">
            {backboneInterfaces.map((iface, index) => (
              <div key={index} className="interface-card">
                <div className="interface-card-header">
                  <span className="interface-name">{iface.interfaceName}</span>
                  <span
                    className="interface-state"
                    style={{ color: getStateColor(iface.state) }}
                  >
                    {iface.state.toUpperCase()}
                  </span>
                </div>
                <div className="interface-card-body">
                  <div className="interface-detail">
                    <span className="detail-label">IP:</span>
                    <span className="detail-value">{iface.ip}</span>
                  </div>
                  <div className="interface-detail">
                    <span className="detail-label">Connected to:</span>
                    <span className="detail-value">{iface.peerHostname}</span>
                  </div>
                  <div className="interface-detail">
                    <span className="detail-label">Type:</span>
                    <span className="detail-value">{formatBGPType(iface.linkType)}</span>
                  </div>
                </div>
                {renderInterfaceStats(iface.interfaceName)}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Customer Interfaces Section */}
      <div className="info-section">
        <h3>Customer Interfaces ({customerInterfaces.length})</h3>
        {customerInterfaces.length === 0 ? (
          <div className="no-interfaces-message">
            <p>No customer interfaces on this router.</p>
            <p className="hint">Customer interfaces connect to end-user networks.</p>
          </div>
        ) : (
          <div className="interfaces-list">
            {customerInterfaces.map((iface) => (
              <div key={iface.interface_name} className="interface-card customer-interface">
                <div className="interface-card-header">
                  <span className="interface-name">{iface.interface_name}</span>
                  {iface.zone && (
                    <span
                      className="zone-badge"
                      style={{
                        backgroundColor: iface.zone_type === 'simple' ? '#2ecc71' : '#e74c3c',
                      }}
                    >
                      {iface.zone_type === 'simple' ? 'Internal' : `VLAN ${iface.vlan_tag}`}
                    </span>
                  )}
                </div>
                <div className="interface-card-body">
                  <div className="interface-detail">
                    <span className="detail-label">Network:</span>
                    <span className="detail-value">{iface.customer_net || 'N/A'}</span>
                  </div>
                  <div className="interface-detail">
                    <span className="detail-label">Zone:</span>
                    <span className="detail-value">{iface.zone || 'None'}</span>
                  </div>
                  {iface.vlan_tag && (
                    <div className="interface-detail">
                      <span className="detail-label">VLAN Tag:</span>
                      <span className="detail-value">{iface.vlan_tag}</span>
                    </div>
                  )}
                </div>
                {renderInterfaceStats(iface.interface_name)}
                <div className="interface-card-actions">
                  <button
                    className="configure-zone-button"
                    onClick={() => setSelectedCustomerInterface(iface)}
                  >
                    Configure connection
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Zone Configuration Modal */}
      {selectedCustomerInterface && (
        <ZoneConfigModal
          hostname={hostname}
          interfaceInfo={selectedCustomerInterface}
          onClose={handleZoneConfigClose}
        />
      )}
    </div>
  );
};
