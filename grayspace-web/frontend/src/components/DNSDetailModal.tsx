/**
 * DNS server detail modal with live zone data and add record form
 */
import React, { useState, useEffect } from 'react';
import { DNSServer, ZoneData } from '../types';
import { ConsoleButton } from './ConsoleButton';
import { apiService } from '../services/api';

interface DNSDetailModalProps {
  server: DNSServer;
  onClose: () => void;
  onOpenConsole: (hostname: string) => void;
}

const RECORD_TYPES = ['A', 'AAAA', 'NS', 'MX', 'CNAME', 'TXT', 'PTR', 'SRV', 'CAA'];

const DATA_PLACEHOLDERS: Record<string, string> = {
  A: 'IP address (e.g., 1.2.3.4)',
  AAAA: 'IPv6 address',
  NS: 'hostname. (e.g., ns1.example.com.)',
  MX: 'priority hostname. (e.g., 10 mail.example.com.)',
  CNAME: 'hostname. (e.g., www.example.com.)',
  TXT: 'text value',
  PTR: 'hostname.',
  SRV: 'priority weight port target.',
  CAA: 'flags tag value',
};

export const DNSDetailModal: React.FC<DNSDetailModalProps> = ({ server, onClose, onOpenConsole }) => {
  const [zones, setZones] = useState<ZoneData[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedZones, setExpandedZones] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');

  // Add record form state
  const [showAddForm, setShowAddForm] = useState(false);
  const [addZone, setAddZone] = useState('');
  const [addType, setAddType] = useState('A');
  const [addName, setAddName] = useState('@');
  const [addTTL, setAddTTL] = useState(3600);
  const [addData, setAddData] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [submitSuccess, setSubmitSuccess] = useState('');

  const loadZones = async () => {
    setLoading(true);
    try {
      const data = await apiService.getDNSZones(server.hostname);
      setZones(data.zones || []);
      if (data.zones && data.zones.length > 0) {
        setExpandedZones(new Set([data.zones[0].name]));
        if (!addZone) {
          setAddZone(data.zones[0].name);
        }
      }
    } catch (err) {
      console.error('Error loading DNS zones:', err);
      setZones([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadZones();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server.hostname]);

  const toggleZone = (zoneName: string) => {
    setExpandedZones((prev) => {
      const next = new Set(prev);
      if (next.has(zoneName)) {
        next.delete(zoneName);
      } else {
        next.add(zoneName);
      }
      return next;
    });
  };

  const filterRecords = (zone: ZoneData) => {
    if (!searchQuery) return zone.records;
    const q = searchQuery.toLowerCase();
    return zone.records.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.type.toLowerCase().includes(q) ||
        r.data.toLowerCase().includes(q)
    );
  };

  const handleAddRecord = async () => {
    if (!addZone || !addData.trim()) return;
    setSubmitting(true);
    setSubmitError('');
    setSubmitSuccess('');
    try {
      const result = await apiService.addDNSRecord(server.hostname, {
        zone: addZone,
        name: addName || '@',
        type: addType,
        data: addData.trim(),
        ttl: addTTL,
      });
      let msg = result.message;
      if (result.warnings && result.warnings.length > 0) {
        msg += ' | Warnings: ' + result.warnings.join(', ');
      }
      setSubmitSuccess(msg);
      setAddData('');
      setAddName('@');
      // Refresh zones
      await loadZones();
    } catch (err: any) {
      setSubmitError(err.message || 'Failed to add record');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="dns-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-header-left">
            <h2>{server.hostname}</h2>
            <ConsoleButton hostname={server.hostname} onOpen={onOpenConsole} />
          </div>
          <button className="close-button" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-content">
          {/* Server Info */}
          <div className="info-section">
            <h3>Server Information</h3>
            <div className="info-grid">
              <span className="info-label">Hostname:</span>
              <span className="info-value">{server.hostname}</span>

              <span className="info-label">Role:</span>
              <span className="info-value">{server.role}</span>

              <span className="info-label">IP Address:</span>
              <span className="info-value">{server.ip_address}</span>

              <span className="info-label">Subnet:</span>
              <span className="info-value">{server.subnet}</span>

              <span className="info-label">Router:</span>
              <span className="info-value">{server.router}</span>

              {server.router_isp && (
                <>
                  <span className="info-label">ISP:</span>
                  <span className="info-value">{server.router_isp}</span>
                </>
              )}

              {server.router_location && (
                <>
                  <span className="info-label">Location:</span>
                  <span className="info-value">{server.router_location.name}</span>
                </>
              )}

              <span className="info-label">Resources:</span>
              <span className="info-value">
                {server.cores} cores, {server.memory}MB RAM, {server.disk}GB disk
              </span>
            </div>
          </div>

          {/* Zones Section */}
          <div className="zones-section">
            <div className="zone-data-header">
              <h3>Zone Data</h3>
              {server.deployed && !loading && zones.length > 0 && (
                <button
                  className="add-record-btn"
                  onClick={() => { setShowAddForm(!showAddForm); setSubmitError(''); setSubmitSuccess(''); }}
                >
                  {showAddForm ? 'Cancel' : 'Add Record'}
                </button>
              )}
            </div>

            {/* Add Record Form */}
            {showAddForm && (
              <div className="add-record-form">
                <div className="form-row">
                  <label>Zone</label>
                  <select value={addZone} onChange={(e) => setAddZone(e.target.value)}>
                    {zones.map((z) => (
                      <option key={z.name} value={z.name}>{z.name}</option>
                    ))}
                  </select>
                </div>
                <div className="form-row">
                  <label>Type</label>
                  <select value={addType} onChange={(e) => setAddType(e.target.value)}>
                    {RECORD_TYPES.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
                <div className="form-row">
                  <label>Name</label>
                  <input
                    type="text"
                    value={addName}
                    onChange={(e) => setAddName(e.target.value)}
                    placeholder="@ or subdomain"
                  />
                </div>
                <div className="form-row">
                  <label>TTL</label>
                  <input
                    type="number"
                    value={addTTL}
                    onChange={(e) => setAddTTL(parseInt(e.target.value) || 3600)}
                    min={1}
                  />
                </div>
                <div className="form-row">
                  <label>Data</label>
                  <input
                    type="text"
                    value={addData}
                    onChange={(e) => setAddData(e.target.value)}
                    placeholder={DATA_PLACEHOLDERS[addType] || 'Record data'}
                  />
                </div>
                <div className="form-actions">
                  <button
                    className="apply-button"
                    onClick={handleAddRecord}
                    disabled={submitting || !addData.trim()}
                  >
                    {submitting ? 'Adding...' : 'Add Record'}
                  </button>
                </div>
                {submitError && <div className="form-error">{submitError}</div>}
                {submitSuccess && <div className="form-success">{submitSuccess}</div>}
              </div>
            )}

            {loading ? (
              <div className="view-loading">
                <div className="spinner"></div>
                <span>Loading zone data...</span>
              </div>
            ) : zones.length === 0 ? (
              <div className="zone-no-data">
                No zone data available. The server may not be configured yet.
              </div>
            ) : (
              <>
                <div className="zone-search">
                  <input
                    type="text"
                    placeholder="Search records..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>

                {zones.map((zone) => {
                  const filtered = filterRecords(zone);
                  const isExpanded = expandedZones.has(zone.name);

                  return (
                    <div key={zone.name} className="zone-collapsible">
                      <div
                        className="zone-collapsible-header"
                        onClick={() => toggleZone(zone.name)}
                      >
                        <h4>
                          <span className="collapse-arrow">
                            {isExpanded ? '\u25BC' : '\u25B6'}
                          </span>{' '}
                          {zone.name} ({filtered.length} records)
                        </h4>
                      </div>

                      {isExpanded && (
                        filtered.length > 0 ? (
                          <table className="zone-records-table">
                            <thead>
                              <tr>
                                <th>Name</th>
                                <th>TTL</th>
                                <th>Type</th>
                                <th>Data</th>
                              </tr>
                            </thead>
                            <tbody>
                              {filtered.map((record, idx) => (
                                <tr key={idx}>
                                  <td>{record.name}</td>
                                  <td>{record.ttl}</td>
                                  <td>{record.type}</td>
                                  <td>{record.data}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        ) : (
                          <div className="zone-no-data">No matching records.</div>
                        )
                      )}
                    </div>
                  );
                })}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
