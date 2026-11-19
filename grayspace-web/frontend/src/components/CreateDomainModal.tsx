/**
 * Modal for creating a new domain with TLD delegation and hosting zone
 */
import React, { useState } from 'react';
import { useTopologyStore } from '../stores/topologyStore';
import { apiService } from '../services/api';
import { CreateDomainResult } from '../types';

interface CreateDomainModalProps {
  onClose: () => void;
}

export const CreateDomainModal: React.FC<CreateDomainModalProps> = ({ onClose }) => {
  const { dnsServers } = useTopologyStore();
  const hostingServers = dnsServers.filter((s) => s.role === 'hosting' && s.deployed);

  const [domain, setDomain] = useState('');
  const [hostingServer, setHostingServer] = useState('');
  const [ipAddress, setIpAddress] = useState('');
  const [createWww, setCreateWww] = useState(true);
  const [mxRecords, setMxRecords] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<CreateDomainResult | null>(null);

  const handleSubmit = async () => {
    if (!domain.trim()) return;
    setSubmitting(true);
    setError('');
    setResult(null);
    try {
      const res = await apiService.createDomain({
        domain: domain.trim(),
        hosting_server: hostingServer || undefined,
        ip_address: ipAddress.trim() || undefined,
        mx_records: mxRecords.trim() || undefined,
        create_www: createWww,
      });
      setResult(res);
    } catch (err: any) {
      setError(err.message || 'Failed to create domain');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="create-domain-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Create Domain</h2>
          <button className="close-button" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-content">
          {result ? (
            <div className="create-domain-result">
              <div className={result.success ? 'form-success' : 'form-error'}>
                {result.message}
              </div>
              {result.success && (
                <div className="result-details">
                  <p><strong>TLD Server:</strong> {result.tld_server}</p>
                  <p><strong>Hosting Server:</strong> {result.hosting_server}</p>
                  <p><strong>Records Created:</strong> {result.records_created}</p>
                  {result.warnings && result.warnings.length > 0 && (
                    <div className="warning-message">
                      {result.warnings.map((w, i) => <p key={i}>{w}</p>)}
                    </div>
                  )}
                </div>
              )}
              <div className="form-actions" style={{ marginTop: '1rem' }}>
                <button className="apply-button" onClick={onClose}>Close</button>
              </div>
            </div>
          ) : (
            <div className="create-domain-form">
              <div className="form-row">
                <label>Domain <span style={{ color: 'var(--gs-bad)' }}>*</span></label>
                <input
                  type="text"
                  value={domain}
                  onChange={(e) => setDomain(e.target.value)}
                  placeholder="example.com"
                />
              </div>

              <div className="form-row">
                <label>Hosting Server</label>
                <select value={hostingServer} onChange={(e) => setHostingServer(e.target.value)}>
                  <option value="">Random</option>
                  {hostingServers.map((s) => (
                    <option key={s.hostname} value={s.hostname}>{s.hostname}</option>
                  ))}
                </select>
              </div>

              <div className="form-row">
                <label>IP Address</label>
                <input
                  type="text"
                  value={ipAddress}
                  onChange={(e) => setIpAddress(e.target.value)}
                  placeholder="IP address for @ record"
                />
              </div>

              <div className="form-row form-row-checkbox">
                <label>
                  <input
                    type="checkbox"
                    checked={createWww}
                    onChange={(e) => setCreateWww(e.target.checked)}
                  />
                  Create www CNAME
                </label>
              </div>

              <div className="form-row">
                <label>MX Records</label>
                <input
                  type="text"
                  value={mxRecords}
                  onChange={(e) => setMxRecords(e.target.value)}
                  placeholder="10 mail.example.com."
                />
              </div>

              <div className="form-actions">
                <button className="clear-button" onClick={onClose}>Cancel</button>
                <button
                  className="apply-button"
                  onClick={handleSubmit}
                  disabled={submitting || !domain.trim()}
                >
                  {submitting ? 'Creating...' : 'Create Domain'}
                </button>
              </div>

              {error && <div className="form-error">{error}</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
