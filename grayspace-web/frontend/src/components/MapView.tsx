/**
 * Main map component with Leaflet
 *
 * Draws the map layer (GET /api/v1/map): router positions,
 * great-circle link curves, ISP and link colors all come from the server, so this map and the
 * GHOSTS map draw the same thing. Features are drawn on multiple "world copies" so trans-Pacific
 * links are visible and the map scrolls seamlessly. Live link states (/ws) update by feature id.
 */
import React, { useEffect, useRef, useState } from 'react';
import { MapContainer, TileLayer, Marker, Polyline, Popup, Tooltip, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import { LatLngBounds } from 'leaflet';
import { useTopologyStore } from '../stores/topologyStore';
import { getLinkColor } from '../utils/colors';
import { formatBGPType } from '../utils/formatters';
import { featuresOfKind, pointLatLng, toLatLngs, unwrapLine, WORLD_OFFSETS, readyToFit } from '../utils/mapLayer';
import { Link } from '../types';
import { Feature, FeatureCollection } from '../types/slices';

interface MapViewProps {
  onRouterSelect: (hostname: string) => void;
  onLinkSelect?: (link: Link) => void;
  onMapClick?: () => void;
  centerOn?: { lat: number; lng: number } | null;
  selectedRouterHostname?: string | null;
  onOpenConsole?: (hostname: string) => void;
  /** Whether the map is on screen. The workspace keeps it mounted but hidden on other tabs. */
  visible?: boolean;
  /** A layer to draw instead of the deployment's (a base version's preview): no live state, nothing attached */
  layer?: FeatureCollection;
}

const NONE = new Set<string>();
const NO_STATES = new Map();

// Fit the map to the routers once they are known and the map is on screen. A map that starts
// hidden (display: none) has no size, so Leaflet is told its size when it shows, and the first
// fit waits for it: fitting a 0x0 map zooms to the street and centers off the routers.
// centering: the map opened to show one place (a site from Enclaves, a router from the dropdown),
// which the fit would undo.
const MapBoundsSetter: React.FC<{ routers: Feature[]; visible: boolean; centering: boolean }> = ({ routers, visible, centering }) => {
  const map = useMap();
  const fitted = useRef(false);

  useEffect(() => {
    if (!visible) return;
    map.invalidateSize();
    if (centering) {
      fitted.current = true;
      return;
    }
    if (readyToFit(visible, map.getSize(), routers.length, fitted.current)) {
      fitted.current = true;
      map.fitBounds(new LatLngBounds(routers.map((r) => pointLatLng(r))), { padding: [50, 50] });
    }
  }, [routers, map, visible, centering]);

  return null;
};

// Component to center map on a specific location
const MapCenterer: React.FC<{ centerOn: { lat: number; lng: number } | null }> = ({ centerOn }) => {
  const map = useMap();

  useEffect(() => {
    if (centerOn) {
      // The map may have only just been shown
      map.invalidateSize();
      map.setView([centerOn.lat, centerOn.lng], 8, { animate: true });
    }
  }, [centerOn, map]);

  return null;
};

const enclaveIcon = (shape: 'site' | 'internet-host', color: string, routed: boolean) =>
  L.divIcon({
    className: `enclave-div-icon enclave-${shape}`,
    html: `<div class="enclave-icon-${shape}${routed ? ' routed' : ''}" style="background-color: ${color};"></div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
    popupAnchor: [0, -10],
  });

// One icon per look, so a marker keeps its element while nothing about it changes: a new icon
// makes Leaflet replace the element, and one replaced under the pointer never gets its mouseout.
// Hover is CSS (.router-div-icon:hover) for the same reason.
const routerIcons = new Map<string, L.DivIcon>();
const routerIcon = (color: string, selected: boolean, notRunning: boolean, attached: number): L.DivIcon => {
  const key = `${color}|${selected}|${notRunning}|${attached}`;
  let icon = routerIcons.get(key);
  if (!icon) {
    const className = ['router-div-icon', selected && 'selected', notRunning && 'not-running'].filter(Boolean).join(' ');
    icon = L.divIcon({
      className,
      html: `
        <div class="router-icon-inner">
          <div class="router-icon-dot" style="
            width: ${selected ? 28 : 24}px;
            height: ${selected ? 28 : 24}px;
            background-color: ${color};
            border: ${selected ? 4 : 2}px solid ${selected ? '#000000' : 'white'};
            opacity: ${selected ? 1 : 0.9};
          "></div>
          ${attached > 0 ? `<span class="router-attached-badge">${attached}</span>` : ''}
        </div>
      `,
      iconSize: [40, 40],
      iconAnchor: [20, 20],
      popupAnchor: [0, -14],
      tooltipAnchor: [0, -14],
    });
    routerIcons.set(key, icon);
  }
  return icon;
};

// Component to handle clicks on empty map space
const MapClickHandler: React.FC<{
  onMapClick?: () => void;
  clickedFeatureRef: React.MutableRefObject<boolean>;
  selectedMarkerRef: React.MutableRefObject<L.Marker | null>;
  selectedMarkerHostnameRef: React.MutableRefObject<string | null>;
}> = ({ onMapClick, clickedFeatureRef, selectedMarkerRef, selectedMarkerHostnameRef }) => {
  useMapEvents({
    click: () => {
      if (clickedFeatureRef.current) {
        clickedFeatureRef.current = false;
        return;
      }
      // Close any pinned popup immediately so it doesn't require a second click
      if (selectedMarkerRef.current) {
        selectedMarkerRef.current.closePopup();
        selectedMarkerRef.current.setZIndexOffset(0);
        selectedMarkerRef.current = null;
        selectedMarkerHostnameRef.current = null;
      }
      onMapClick?.();
    },
  });
  return null;
};

export const MapView: React.FC<MapViewProps> = ({ onRouterSelect, onLinkSelect, onMapClick, centerOn, selectedRouterHostname, onOpenConsole, visible = true, layer }) => {
  const store = useTopologyStore();
  const mapLayer = layer ?? store.mapLayer;
  const linkStates = layer ? NO_STATES : store.linkStates;
  const hiddenISPs = layer ? NONE : store.hiddenISPs;
  const getRouterAttachedItems = layer ? () => [] : store.getRouterAttachedItems;
  const getRouterByHostname = layer ? () => undefined : store.getRouterByHostname;
  const getLinkById = layer ? () => undefined : store.getLinkById;
  const [hoveredLink, setHoveredLink] = useState<string | null>(null);
  const selectedMarkerRef = useRef<L.Marker | null>(null);
  const selectedMarkerHostnameRef = useRef<string | null>(null);
  const clickedFeatureRef = useRef(false);

  const routerFeatures = featuresOfKind(mapLayer, 'router');
  const linkFeatures = featuresOfKind(mapLayer, 'link');
  const ispOf = new Map(routerFeatures.map((r) => [r.id, r.properties.isp ?? '']));

  // Close pinned popup when panel closes or selection changes
  useEffect(() => {
    if (!selectedRouterHostname && selectedMarkerRef.current) {
      selectedMarkerRef.current.closePopup();
      selectedMarkerRef.current = null;
      selectedMarkerHostnameRef.current = null;
    } else if (
      selectedMarkerHostnameRef.current &&
      selectedMarkerHostnameRef.current !== selectedRouterHostname
    ) {
      if (selectedMarkerRef.current) {
        selectedMarkerRef.current.closePopup();
        selectedMarkerRef.current = null;
        selectedMarkerHostnameRef.current = null;
      }
    }
  }, [selectedRouterHostname]);

  // The live state from /ws wins over the state the layer was fetched with.
  const linkState = (feature: Feature): string => linkStates.get(feature.id)?.state || feature.properties.state || 'unknown';
  const linkColor = (feature: Feature): string =>
    linkStates.has(feature.id) ? getLinkColor(linkState(feature), feature.properties.type) : feature.properties.color || getLinkColor(linkState(feature), feature.properties.type);

  // The topology link for the traffic panel, or one built from the feature when there is none.
  const linkOf = (feature: Feature): Link =>
    getLinkById(feature.id) ?? {
      id: feature.id,
      source_hostname: feature.properties.source ?? '',
      target_hostname: feature.properties.target ?? '',
      type: (feature.properties.type ?? 'INTERNAL_LINK') as Link['type'],
      subnet: '',
      source_ip: '',
      target_ip: '',
    };

  const enclaveVisible = (feature: Feature) => {
    const routers = feature.properties.routers ?? (feature.properties.router ? [feature.properties.router] : []);
    return !routers.every((r) => hiddenISPs.has(ispOf.get(r) ?? ''));
  };

  return (
    <MapContainer
      center={[20, 0]}
      zoom={2}
      style={{ height: '100%', width: '100%' }}
      minZoom={2}
      maxZoom={18}
      worldCopyJump={true}
    >
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      />

      <MapBoundsSetter routers={routerFeatures} visible={visible} centering={Boolean(centerOn)} />
      <MapCenterer centerOn={centerOn || null} />
      <MapClickHandler
        onMapClick={onMapClick}
        clickedFeatureRef={clickedFeatureRef}
        selectedMarkerRef={selectedMarkerRef}
        selectedMarkerHostnameRef={selectedMarkerHostnameRef}
      />

      {/* Draw links on all world copies */}
      {linkFeatures.flatMap((feature) => {
        const { source, target, type } = feature.properties;
        // Hide link if either endpoint's ISP is hidden
        if (hiddenISPs.has(ispOf.get(source ?? '') ?? '') || hiddenISPs.has(ispOf.get(target ?? '') ?? '')) {
          return [];
        }

        const link = linkOf(feature);
        const state = linkState(feature);
        const color = linkColor(feature);
        const isHovered = hoveredLink === feature.id;
        const line = unwrapLine(feature.geometry);

        return WORLD_OFFSETS.flatMap((offset) => {
          const positions = toLatLngs(line, offset);
          return [
            // Invisible wide polyline for easier clicking
            <Polyline
              key={`${feature.id}-${offset}-hitarea`}
              positions={positions}
              color="transparent"
              weight={20}
              opacity={0}
              eventHandlers={{
                click: () => { clickedFeatureRef.current = true; onLinkSelect?.(link); },
                mouseover: () => setHoveredLink(feature.id),
                mouseout: () => setHoveredLink(null),
              }}
            >
              <Tooltip sticky>
                <div style={{ minWidth: '160px' }}>
                  <strong>{source} ↔ {target}</strong>
                  <br />
                  Type: {formatBGPType(type ?? 'INTERNAL_LINK')}
                  {link.subnet && (<><br />Subnet: {link.subnet}</>)}
                </div>
              </Tooltip>
            </Polyline>,
            // Dark outline (only visible on hover)
            ...(isHovered ? [
              <Polyline
                key={`${feature.id}-${offset}-outline`}
                positions={positions}
                color="#2c3e50"
                weight={7}
                opacity={1}
                interactive={false}
              />
            ] : []),
            // Visible polyline
            <Polyline
              key={`${feature.id}-${offset}`}
              positions={positions}
              color={isHovered ? '#ffffff' : color}
              weight={isHovered ? 4 : 3}
              opacity={isHovered ? 1 : 0.7}
              interactive={false}
            >
              <Popup>
                <div style={{ minWidth: '200px' }}>
                  <strong>{source} ↔ {target}</strong>
                  <br />
                  Type: {formatBGPType(type ?? 'INTERNAL_LINK')}
                  {link.subnet && (<><br />Subnet: {link.subnet}</>)}
                  <br />
                  State: <span style={{ color }}>{state.toUpperCase()}</span>
                  {link.ospf_area && (
                    <>
                      <br />
                      OSPF Area: {link.ospf_area}
                    </>
                  )}
                </div>
              </Popup>
            </Polyline>
          ];
        });
      })}

      {/* Enclave attachments: dashed lines from sites and internet hosts to their routers */}
      {featuresOfKind(mapLayer, 'attachment').filter(enclaveVisible).flatMap((feature) => {
        const line = unwrapLine(feature.geometry);
        return WORLD_OFFSETS.map((offset) => (
          <Polyline
            key={`${feature.id}-${offset}`}
            positions={toLatLngs(line, offset)}
            color="#ff00aa"
            weight={2}
            opacity={0.8}
            dashArray="4 4"
            interactive={false}
          />
        ));
      })}

      {/* Enclave sites and internet hosts */}
      {[...featuresOfKind(mapLayer, 'site'), ...featuresOfKind(mapLayer, 'internet-host')].filter(enclaveVisible).flatMap((feature) => {
        const p = feature.properties;
        const isSite = p.kind === 'site';
        const icon = enclaveIcon(isSite ? 'site' : 'internet-host', p.color || '#ff00aa', Boolean(p.routed));
        return WORLD_OFFSETS.map((offset) => (
          <Marker key={`${feature.id}-${offset}`} position={pointLatLng(feature, offset)} icon={icon}>
            <Popup>
              <div style={{ minWidth: '200px' }}>
                <strong style={{ fontSize: '1.05em' }}>{p.instance}/{p.name}</strong>
                <br />
                {isSite ? (p.routed ? 'Routed site' : 'NAT site') : 'Internet host'} — instance {p.instance} ({String(p.state ?? '')})
                <br />
                {isSite ? (
                  <>
                    Edge: {p.edge}
                    <br />
                    Attached to: {(p.routers ?? [p.router]).join(', ')} (WAN {p.wan_ip})
                  </>
                ) : (
                  <>
                    {p.hostname} {p.ip} on {p.router}
                    {(p.fqdns ?? []).map((name) => (<div key={name} style={{ fontSize: '0.85em', color: 'var(--gs-muted)' }}>{name}</div>))}
                  </>
                )}
              </div>
            </Popup>
          </Marker>
        ));
      })}

      {/* Draw router markers on all world copies */}
      {routerFeatures.flatMap((feature) => {
        const hostname = feature.id;
        const p = feature.properties;
        // Hide router if its ISP is hidden
        if (hiddenISPs.has(p.isp ?? '')) {
          return [];
        }

        const router = getRouterByHostname(hostname);
        const color = p.color || '#95a5a6';
        const isSelected = selectedRouterHostname === hostname;
        const attachedItems = getRouterAttachedItems(hostname);
        const hasAttached = attachedItems.length > 0;
        const icon = routerIcon(color, isSelected, Boolean(p.status && p.status !== 'running' && p.status !== 'unknown'), attachedItems.length);

        const summary = (
          <div style={{ minWidth: '200px' }}>
            <strong style={{ fontSize: '1.1em' }}>{hostname}</strong>
            <br />
            ISP: {p.isp} (AS{p.asn})
            <br />
            Location: {p.location}
            {p.status && p.status !== 'running' && (<><br />Status: {p.status}</>)}
            {router && (
              <>
                <br />
                Loopback: {router.loopback_ip}
                {router.roles.length > 0 && (
                  <>
                    <br />
                    Roles: {router.roles.join(', ')}
                  </>
                )}
              </>
            )}
            {hasAttached && (
              <>
                <br />
                <strong style={{ fontSize: '0.9em', marginTop: '4px', display: 'inline-block' }}>Connected:</strong>
                {attachedItems.slice(0, 3).map((item) => (
                  <div key={item.hostname} style={{ fontSize: '0.85em', color: 'var(--gs-muted)', paddingLeft: '4px' }}>
                    {item.hostname} ({item.ip_address})
                  </div>
                ))}
                {attachedItems.length > 3 && (
                  <div style={{ fontSize: '0.85em', color: 'var(--gs-muted)', paddingLeft: '4px' }}>
                    + {attachedItems.length - 3} more
                  </div>
                )}
              </>
            )}
          </div>
        );

        return WORLD_OFFSETS.map((offset) => (
          <Marker
            key={`${hostname}-${offset}`}
            position={pointLatLng(feature, offset)}
            icon={icon}
            eventHandlers={{
              click: (e) => {
                clickedFeatureRef.current = true;
                // Reset previous selected marker's z-index
                if (selectedMarkerRef.current && selectedMarkerHostnameRef.current !== hostname) {
                  selectedMarkerRef.current.setZIndexOffset(0);
                  selectedMarkerRef.current.closePopup();
                }
                // Store new marker ref and bring to front
                selectedMarkerRef.current = e.target as L.Marker;
                selectedMarkerHostnameRef.current = hostname;
                (e.target as L.Marker).setZIndexOffset(1000);
                e.target.openPopup();
                onRouterSelect(hostname);
              },
              mouseover: (e) => {
                // Bring marker to front so co-located nodes don't overlap
                (e.target as L.Marker).setZIndexOffset(1000);
                // A router selected from the dropdown pins its summary when the pointer finds it
                if (isSelected && !selectedMarkerRef.current) {
                  selectedMarkerRef.current = e.target as L.Marker;
                  selectedMarkerHostnameRef.current = hostname;
                  e.target.openPopup();
                }
              },
              mouseout: (e) => {
                if (!isSelected) (e.target as L.Marker).setZIndexOffset(0);
              },
            }}
          >
            {/* Hovering shows the summary as a tooltip, which closes itself; a click pins it as a popup */}
            {!isSelected && <Tooltip direction="top" opacity={1}>{summary}</Tooltip>}
            <Popup closeButton={false} autoClose={false} closeOnClick={false}>{summary}</Popup>
          </Marker>
        ));
      })}
    </MapContainer>
  );
};
