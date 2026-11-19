/**
 * CA server detail modal with certificate list and issue/revoke actions
 */
import React, { useState, useEffect } from 'react';
import { CAServer, Certificate } from '../types';
import { ConsoleButton } from './ConsoleButton';
import { apiService } from '../services/api';

interface CADetailModalProps {
  server: CAServer;
  onClose: () => void;
  onOpenConsole: (hostname: string) => void;
}

export const CADetailModal: React.FC<CADetailModalProps> = ({ server, onClose, onOpenConsole }) => {
  const [certificates, setCertificates] = useState<Certificate[]>([]);
  const [loading, setLoading] = useState(true);

  const [issueDomain, setIssueDomain] = useState('');
  const [issueLoading, setIssueLoading] = useState(false);
  const [issueResult, setIssueResult] = useState<{ success: boolean; message: string; certificate?: string; private_key?: string; domain?: string } | null>(null);

  const loadCertificates = async () => {
    setLoading(true);
    try {
      const data = await apiService.getCACertificates(server.hostname);
      setCertificates(data.certificates || []);
    } catch (err) {
      console.error('Error loading certificates:', err);
      setCertificates([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCertificates();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server.hostname]);

  const handleIssueCert = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!issueDomain.trim()) return;
    setIssueLoading(true);
    setIssueResult(null);
    try {
      const domain = issueDomain.trim();
      const result = await apiService.issueCertificate(domain, server.hostname);
      setIssueResult({
        success: result.success,
        message: result.message,
        certificate: result.certificate,
        private_key: result.private_key,
        domain,
      });
      if (result.success) {
        setIssueDomain('');
        await loadCertificates();
      }
    } catch (err) {
      setIssueResult({ success: false, message: err instanceof Error ? err.message : 'Request failed' });
    } finally {
      setIssueLoading(false);
    }
  };

  const handleRevoke = async (serial: string) => {
    if (!window.confirm('Revoke this certificate?')) return;
    try {
      const result = await apiService.revokeCertificate(server.hostname, serial);
      if (result.success) {
        await loadCertificates();
      }
    } catch (err) {
      console.error('Revoke failed:', err);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="dns-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-header-left">
            <h2>{server.ca_name}</h2>
            <ConsoleButton hostname={server.hostname} onOpen={onOpenConsole} />
          </div>
          <button className="close-button" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-content">
          {/* Server Info */}
          <div className="info-section">
            <h3>CA Information</h3>
            <div className="info-grid">
              <span className="info-label">CA Name:</span>
              <span className="info-value">{server.ca_name}</span>

              <span className="info-label">Hostname:</span>
              <span className="info-value">{server.hostname}</span>

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

              <span className="info-label">Status:</span>
              <span className="info-value">
                <span className={`deployed-badge ${server.deployed ? 'deployed' : 'not-deployed'}`}>
                  {server.deployed ? 'deployed' : 'pending'}
                </span>
              </span>
            </div>
          </div>

          {/* Issue Certificate Form */}
          {server.deployed && (
            <div className="info-section">
              <h3>Issue Certificate</h3>
              <form onSubmit={handleIssueCert} className="ca-issue-form">
                <input
                  type="text"
                  value={issueDomain}
                  onChange={(e) => setIssueDomain(e.target.value)}
                  placeholder="Domain name (e.g. example.com)"
                  className="ca-issue-input"
                  disabled={issueLoading}
                />
                <button
                  type="submit"
                  className="create-domain-btn"
                  disabled={issueLoading || !issueDomain.trim()}
                >
                  {issueLoading ? 'Issuing...' : 'Issue'}
                </button>
              </form>
              {issueResult && (
                <div className={`ca-issue-result ${issueResult.success ? 'success' : 'error'}`}>
                  <p>{issueResult.message}</p>
                  {issueResult.success && issueResult.certificate && (
                    <div className="ca-download-links">
                      <a
                        href={`data:application/x-pem-file;charset=utf-8,${encodeURIComponent(issueResult.certificate)}`}
                        download={`${issueResult.domain}.crt`}
                        className="ca-download-btn"
                      >
                        Download Certificate (.crt)
                      </a>
                      <a
                        href={`data:application/x-pem-file;charset=utf-8,${encodeURIComponent(issueResult.private_key || '')}`}
                        download={`${issueResult.domain}.key`}
                        className="ca-download-btn"
                      >
                        Download Private Key (.key)
                      </a>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Certificates Table */}
          <div className="info-section">
            <h3>Issued Certificates ({certificates.length})</h3>
            {loading ? (
              <p style={{ color: 'var(--gs-muted)', fontStyle: 'italic' }}>Loading certificates...</p>
            ) : certificates.length === 0 ? (
              <p style={{ color: 'var(--gs-muted)', fontStyle: 'italic' }}>No certificates issued yet.</p>
            ) : (
              <table className="zone-records-table">
                <thead>
                  <tr>
                    <th>Subject</th>
                    <th>Serial</th>
                    <th>Issued</th>
                    <th>Expires</th>
                    <th>Status</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {certificates.map((cert) => (
                    <tr key={cert.serial}>
                      <td><strong>{cert.subject}</strong></td>
                      <td style={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>
                        {cert.serial.length > 20 ? cert.serial.substring(0, 20) + '...' : cert.serial}
                      </td>
                      <td>{cert.not_before}</td>
                      <td>{cert.not_after}</td>
                      <td>
                        <span className={`deployed-badge ${cert.status === 'active' ? 'deployed' : 'not-deployed'}`}>
                          {cert.status}
                        </span>
                      </td>
                      <td>
                        {cert.status === 'active' && (
                          <button
                            className="ca-revoke-btn"
                            onClick={() => handleRevoke(cert.serial)}
                          >
                            Revoke
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
