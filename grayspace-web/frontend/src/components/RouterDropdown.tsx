/**
 * Router search/navigation dropdown component
 */
import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useTopologyStore } from '../stores/topologyStore';

interface RouterDropdownProps {
  onRouterSelect: (hostname: string, lat: number, lng: number) => void;
}

export const RouterDropdown: React.FC<RouterDropdownProps> = ({ onRouterSelect }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { routers, hiddenISPs, ispColor } = useTopologyStore();

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

  // Focus input when dropdown opens
  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  // Filter routers based on search term and visibility
  const filteredRouters = useMemo(() => {
    const visible = routers.filter((r) => !hiddenISPs.has(r.isp_name));
    if (!searchTerm) return visible;

    const term = searchTerm.toLowerCase();
    return visible.filter(
      (r) =>
        r.hostname.toLowerCase().includes(term) ||
        r.isp_name.toLowerCase().includes(term) ||
        r.location.name.toLowerCase().includes(term)
    );
  }, [routers, hiddenISPs, searchTerm]);

  const visibleCount = routers.filter((r) => !hiddenISPs.has(r.isp_name)).length;

  const handleRouterClick = (router: typeof routers[0]) => {
    onRouterSelect(router.hostname, router.location.latitude, router.location.longitude);
    setIsOpen(false);
    setSearchTerm('');
  };

  return (
    <div className="header-dropdown" ref={dropdownRef}>
      <button
        className="dropdown-trigger"
        onClick={() => setIsOpen(!isOpen)}
      >
        {visibleCount} Routers
        <span className="dropdown-arrow">{isOpen ? '▲' : '▼'}</span>
      </button>

      {isOpen && (
        <div className="dropdown-menu router-dropdown-menu">
          <div className="dropdown-search">
            <input
              ref={inputRef}
              type="text"
              placeholder="Search routers..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
          <div className="dropdown-list">
            {filteredRouters.length === 0 ? (
              <div className="dropdown-empty">No routers found</div>
            ) : (
              filteredRouters.map((router) => {
                const color = ispColor(router.isp_name);

                return (
                  <button
                    key={router.hostname}
                    className="dropdown-item router-item"
                    onClick={() => handleRouterClick(router)}
                  >
                    <span
                      className="isp-color-indicator"
                      style={{ backgroundColor: color }}
                    />
                    <span className="dropdown-item-label">{router.hostname}</span>
                    <span className="dropdown-item-detail">{router.isp_name}</span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
};
