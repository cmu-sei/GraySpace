/**
 * ISP visibility dropdown component
 */
import React, { useState, useRef, useEffect } from 'react';
import { useTopologyStore } from '../stores/topologyStore';

export const ISPDropdown: React.FC = () => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const { isps, hiddenISPs, toggleISPVisibility, showAllISPs, hideAllISPs, ispColor } = useTopologyStore();

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const visibleCount = isps.length - hiddenISPs.size;

  return (
    <div className="header-dropdown" ref={dropdownRef}>
      <button
        className="dropdown-trigger"
        onClick={() => setIsOpen(!isOpen)}
      >
        {visibleCount} ISPs
        <span className="dropdown-arrow">{isOpen ? '▲' : '▼'}</span>
      </button>

      {isOpen && (
        <div className="dropdown-menu">
          <div className="dropdown-actions">
            <button onClick={showAllISPs}>Show All</button>
            <button onClick={hideAllISPs}>Hide All</button>
          </div>
          <div className="dropdown-list">
            {isps.map((isp) => {
              const isVisible = !hiddenISPs.has(isp.name);
              const color = ispColor(isp.name);

              return (
                <label key={isp.name} className="dropdown-item">
                  <input
                    type="checkbox"
                    checked={isVisible}
                    onChange={() => toggleISPVisibility(isp.name)}
                  />
                  <span
                    className="isp-color-indicator"
                    style={{ backgroundColor: color }}
                  />
                  <span className="dropdown-item-label">{isp.name}</span>
                  <span className="dropdown-item-detail">AS{isp.asn}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
