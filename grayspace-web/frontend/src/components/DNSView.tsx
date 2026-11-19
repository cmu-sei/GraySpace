/**
 * DNS tab view - shows all DNS servers grouped by role
 */
import React, { useState } from 'react';
import { useTopologyStore } from '../stores/topologyStore';
import { DNSServer } from '../types';
import { ConsoleButton } from './ConsoleButton';
import { DNSDetailModal } from './DNSDetailModal';
import { CreateDomainModal } from './CreateDomainModal';

interface DNSViewProps {
  onOpenConsole: (hostname: string) => void;
}

const ROLE_ORDER: Array<{ role: string; label: string }> = [
  { role: 'root-dns', label: 'Root DNS Servers' },
  { role: 'tld', label: 'TLD Servers' },
  { role: 'hosting', label: 'Hosting DNS Servers' },
  { role: 'resolver', label: 'Resolvers / Caching Servers' },
];

export const DNSView: React.FC<DNSViewProps> = ({ onOpenConsole }) => {
  const { dnsServers } = useTopologyStore();
  const [selectedServer, setSelectedServer] = useState<DNSServer | null>(null);
  const [showCreateDomain, setShowCreateDomain] = useState(false);

  // Group servers by role
  const grouped: Record<string, DNSServer[]> = {};
  for (const server of dnsServers) {
    const role = server.role || 'unknown';
    if (!grouped[role]) grouped[role] = [];
    grouped[role].push(server);
  }

  if (dnsServers.length === 0) {
    return (
      <div className="dns-view">
        <h2>DNS Servers</h2>
        <div className="view-empty">No DNS servers found in the topology.</div>
      </div>
    );
  }

  return (
    <div className="dns-view">
      <div className="dns-view-header">
        <h2>DNS Servers ({dnsServers.length})</h2>
        <button className="create-domain-btn" onClick={() => setShowCreateDomain(true)}>
          Create Domain
        </button>
      </div>

      {ROLE_ORDER.map(({ role, label }) => {
        const servers = grouped[role];
        if (!servers || servers.length === 0) return null;

        return (
          <div key={role} className="dns-role-section">
            <h3>{label} ({servers.length})</h3>
            <div className="dns-cards-grid">
              {servers.map((server) => (
                <div
                  key={server.hostname}
                  className={`dns-card role-${server.role}`}
                  onClick={() => setSelectedServer(server)}
                >
                  <div className="dns-card-header">
                    <span className="dns-card-hostname">{server.hostname}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <span className={`deployed-badge ${server.deployed ? 'deployed' : 'not-deployed'}`}>
                        {server.deployed ? 'deployed' : 'pending'}
                      </span>
                      <ConsoleButton hostname={server.hostname} onOpen={onOpenConsole} />
                    </div>
                  </div>
                  <div className="dns-card-body">
                    <p><strong>IP:</strong> {server.ip_address}</p>
                    <p><strong>Router:</strong> {server.router}</p>
                    {server.router_isp && (
                      <p><strong>ISP:</strong> {server.router_isp}</p>
                    )}
                    {server.router_location && (
                      <p><strong>Location:</strong> {server.router_location.name}</p>
                    )}
                    {server.zones && server.zones.length > 0 && (
                      <div className="dns-card-zones">
                        {server.zones.map((zone) => (
                          <span key={zone} className="zone-tag">{zone}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {/* Detail Modal */}
      {selectedServer && (
        <DNSDetailModal
          server={selectedServer}
          onClose={() => setSelectedServer(null)}
          onOpenConsole={onOpenConsole}
        />
      )}

      {/* Create Domain Modal */}
      {showCreateDomain && (
        <CreateDomainModal onClose={() => setShowCreateDomain(false)} />
      )}
    </div>
  );
};
