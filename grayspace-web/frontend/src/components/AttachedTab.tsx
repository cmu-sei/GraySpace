/**
 * Attached tab for the router detail panel: the core DNS servers and CAs on the router's customer
 * network
 */
import React, { useState, useEffect } from 'react';
import { RouterAttached } from '../types';
import { ConsoleButton } from './ConsoleButton';
import { apiService } from '../services/api';

interface AttachedTabProps {
  hostname: string;
  onOpenConsole: (hostname: string) => void;
}

export const AttachedTab: React.FC<AttachedTabProps> = ({ hostname, onOpenConsole }) => {
  const [items, setItems] = useState<RouterAttached[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        setItems(await apiService.getRouterAttached(hostname));
      } catch (err) {
        console.error('Error loading what is attached to the router:', err);
        setItems([]);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [hostname]);

  if (loading) {
    return (
      <div className="attached-tab">
        <div className="view-loading">
          <div className="spinner"></div>
          <span>Loading...</span>
        </div>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="attached-tab">
        <div className="view-empty">No DNS servers or CAs on this router.</div>
      </div>
    );
  }

  return (
    <div className="attached-tab">
      <div className="attached-tab-list">
        {items.map((item) => (
          <div key={item.hostname} className={`attached-tab-card category-${item.category}`}>
            <div className="attached-tab-card-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span className="interface-name">{item.hostname}</span>
                <span className={`category-badge ${item.category}`}>{item.category}</span>
              </div>
              <ConsoleButton hostname={item.hostname} onOpen={onOpenConsole} />
            </div>
            <div className="attached-tab-card-body">
              <p><strong>Type:</strong> {item.type}</p>
              <p><strong>IP:</strong> {item.ip_address}</p>
              <p><strong>Subnet:</strong> {item.subnet}</p>
              {item.role && <p><strong>Role:</strong> {item.role}</p>}
              {item.zones && item.zones.length > 0 && (
                <p><strong>Zones:</strong> {item.zones.join(', ')}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
