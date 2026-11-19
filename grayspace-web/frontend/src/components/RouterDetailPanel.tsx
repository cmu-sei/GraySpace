/**
 * Router detail panel component
 */
import React, { useState, useEffect } from 'react';
import { RouterDetail, RouterLiveState } from '../types';
import { InterfacesTab } from './InterfacesTab';
import { AttachedTab } from './AttachedTab';
import { ConsoleButton } from './ConsoleButton';
import { formatBGPType } from '../utils/formatters';
import { getStateColor } from '../utils/colors';
import { apiService } from '../services/api';

interface RouterDetailPanelProps {
  router: RouterDetail | null;
  onClose: () => void;
  onOpenConsole: (hostname: string) => void;
}

type ActivePanel = 'info' | 'interfaces' | 'attached';

export const RouterDetailPanel: React.FC<RouterDetailPanelProps> = ({ router, onClose, onOpenConsole }) => {
  const [activePanel, setActivePanel] = useState<ActivePanel>('info');
  const [routerState, setRouterState] = useState<RouterLiveState | null>(null);
  const [bgpExpanded, setBgpExpanded] = useState(false);
  const [ospfExpanded, setOspfExpanded] = useState(false);

  useEffect(() => {
    if (!router || activePanel !== 'info') return;

    const loadState = async () => {
      try {
        const state = await apiService.getRouterState(router.hostname);
        setRouterState(state);
      } catch {
        setRouterState(null);
      }
    };

    loadState();
  }, [router, activePanel]);

  // Reset state when router changes
  useEffect(() => {
    setRouterState(null);
    setBgpExpanded(false);
    setOspfExpanded(false);
  }, [router?.hostname]);

  if (!router) {
    return null;
  }

  const getBGPPeerState = (neighbor: { hostname: string; ip: string }) => {
    if (!routerState) return undefined;
    return routerState.bgp_peers.find(
      (p) => p.peer_hostname === neighbor.hostname || p.peer_address === neighbor.ip
    );
  };

  return (
    <div className="side-panel">
      <div className="panel-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <h2>{router.hostname}</h2>
          <ConsoleButton hostname={router.hostname} onOpen={onOpenConsole} />
        </div>
        <button className="close-button" onClick={onClose}>
          ×
        </button>
      </div>

      {/* Tab Buttons */}
      <div className="panel-tabs">
        <button
          className={`tab-button ${activePanel === 'info' ? 'active' : ''}`}
          onClick={() => setActivePanel('info')}
        >
          Info
        </button>
        <button
          className={`tab-button ${activePanel === 'interfaces' ? 'active' : ''}`}
          onClick={() => setActivePanel('interfaces')}
        >
          Interfaces
        </button>
        <button
          className={`tab-button ${activePanel === 'attached' ? 'active' : ''}`}
          onClick={() => setActivePanel('attached')}
        >
          Attached
        </button>
      </div>

      {/* Info Panel */}
      {activePanel === 'info' && (
        <div className="panel-content">
          {/* Basic Information */}
          <div className="info-section">
            <h3>Basic Information</h3>
            <div className="info-grid">
              <span className="info-label">Hostname:</span>
              <span className="info-value">{router.hostname}</span>

              <span className="info-label">ISP:</span>
              <span className="info-value">{router.isp_name}</span>

              <span className="info-label">ASN:</span>
              <span className="info-value">{router.asn}</span>

              <span className="info-label">Location:</span>
              <span className="info-value">{router.location.name}</span>

              <span className="info-label">Loopback IP:</span>
              <span className="info-value">{router.loopback_ip}</span>
            </div>
          </div>

          {/* Roles */}
          {router.roles && router.roles.length > 0 && (
            <div className="info-section">
              <h3>Roles</h3>
              <div className="tags">
                {router.roles.map((role) => (
                  <span
                    key={role}
                    className="tag"
                    title={role === 'ABR' ? 'Area Border Router' : role === 'RR' ? 'Route Reflector' : undefined}
                  >
                    {role}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* OSPF Areas */}
          {router.ospf_areas && router.ospf_areas.length > 0 && (
            <div className="info-section">
              <h3>OSPF Areas</h3>
              <div className="tags">
                {router.ospf_areas.map((area) => (
                  <span key={area} className="tag" style={{ backgroundColor: '#2ea043' }}>
                    Area {area}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* BGP Neighbors (collapsible) */}
          {router.neighbors && router.neighbors.length > 0 && (
            <div className="info-section">
              <h3
                className="collapsible-header"
                onClick={() => setBgpExpanded(!bgpExpanded)}
              >
                <span className="collapse-arrow">{bgpExpanded ? '\u25BC' : '\u25B6'}</span>
                BGP Neighbors ({router.neighbors.length})
              </h3>
              {bgpExpanded && (
                <ul className="neighbors-list">
                  {router.neighbors.map((neighbor, index) => {
                    const peerState = getBGPPeerState(neighbor);
                    return (
                      <li key={index} className="neighbor-item">
                        <div className="neighbor-header">
                          <strong>{neighbor.hostname}</strong>
                          {peerState && (
                            <span
                              className="state-badge"
                              style={{ backgroundColor: getStateColor(peerState.state) }}
                            >
                              {peerState.state.toUpperCase()}
                            </span>
                          )}
                        </div>
                        <div className="neighbor-details">
                          IP: {neighbor.ip}
                          <br />
                          AS: {neighbor.asn}
                          <br />
                          Type: {formatBGPType(neighbor.type)}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          )}

          {/* OSPF Neighbors (collapsible) */}
          {routerState && routerState.ospf_neighbors.length > 0 && (
            <div className="info-section">
              <h3
                className="collapsible-header"
                onClick={() => setOspfExpanded(!ospfExpanded)}
              >
                <span className="collapse-arrow">{ospfExpanded ? '\u25BC' : '\u25B6'}</span>
                OSPF Neighbors ({routerState.ospf_neighbors.length})
              </h3>
              {ospfExpanded && (
                <ul className="neighbors-list">
                  {routerState.ospf_neighbors.map((neighbor, index) => (
                    <li key={index} className="neighbor-item">
                      <div className="neighbor-header">
                        <strong>{neighbor.neighbor_hostname}</strong>
                        <span
                          className="state-badge"
                          style={{ backgroundColor: getStateColor(neighbor.state) }}
                        >
                          {neighbor.state.toUpperCase()}
                        </span>
                      </div>
                      <div className="neighbor-details">
                        IP: {neighbor.neighbor_address}
                        <br />
                        Router ID: {neighbor.neighbor_id}
                        <br />
                        Area: {neighbor.area}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      {/* Interfaces Tab */}
      {activePanel === 'interfaces' && (
        <InterfacesTab hostname={router.hostname} />
      )}

      {/* Attached Tab */}
      {activePanel === 'attached' && (
        <AttachedTab hostname={router.hostname} onOpenConsole={onOpenConsole} />
      )}
    </div>
  );
};
