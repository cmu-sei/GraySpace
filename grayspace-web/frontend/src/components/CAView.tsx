/**
 * CA tab view - shows all Certificate Authority servers with clickable cards
 */
import React, { useState } from 'react';
import { useTopologyStore } from '../stores/topologyStore';
import { CAServer } from '../types';
import { ConsoleButton } from './ConsoleButton';
import { CADetailModal } from './CADetailModal';

interface CAViewProps {
  onOpenConsole: (hostname: string) => void;
}

export const CAView: React.FC<CAViewProps> = ({ onOpenConsole }) => {
  const { caServers } = useTopologyStore();
  const [selectedCA, setSelectedCA] = useState<CAServer | null>(null);

  if (caServers.length === 0) {
    return (
      <div className="dns-view">
        <h2>Certificate Authorities</h2>
        <div className="view-empty">No CA servers found in the topology.</div>
      </div>
    );
  }

  return (
    <div className="dns-view">
      <div className="dns-view-header">
        <h2>Certificate Authorities ({caServers.length})</h2>
      </div>

      <div className="dns-role-section">
        <h3 className="dns-role-title">CA Servers</h3>
        <div className="dns-cards-grid">
          {caServers.map((server) => (
            <div
              key={server.hostname}
              className="dns-card role-ca"
              onClick={() => setSelectedCA(server)}
            >
              <div className="dns-card-header">
                <span className="dns-card-hostname">{server.ca_name}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <span className={`deployed-badge ${server.deployed ? 'deployed' : 'not-deployed'}`}>
                    {server.deployed ? 'deployed' : 'pending'}
                  </span>
                  <ConsoleButton hostname={server.hostname} onOpen={onOpenConsole} />
                </div>
              </div>
              <div className="dns-card-body">
                <p><strong>Hostname:</strong> {server.hostname}</p>
                <p><strong>IP:</strong> {server.ip_address?.split('/')[0]}</p>
                <p><strong>Router:</strong> {server.router}</p>
                {server.router_location && (
                  <p><strong>Location:</strong> {server.router_location.name}</p>
                )}
                {server.router_isp && (
                  <p><strong>ISP:</strong> {server.router_isp}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {selectedCA && (
        <CADetailModal
          server={selectedCA}
          onClose={() => setSelectedCA(null)}
          onOpenConsole={onOpenConsole}
        />
      )}
    </div>
  );
};
