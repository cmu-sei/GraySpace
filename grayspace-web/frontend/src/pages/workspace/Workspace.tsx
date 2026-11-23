/**
 * A deployment's views: the map, enclave instances, DNS, certificates, and the enclave
 * library under Build. The section comes from the route (/d/:dep/:section); the deployment shell
 * remounts this for each deployment, with the API pointed at it.
 */
import React, { Suspense, useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RouterDetailPanel } from '../../components/RouterDetailPanel';
import { LinkTrafficPanel } from '../../components/LinkTrafficPanel';
import { ResizablePanel } from '../../components/ResizablePanel';
import { PresetModal } from '../../components/PresetModal';
import { ISPDropdown } from '../../components/ISPDropdown';
import { RouterDropdown } from '../../components/RouterDropdown';
import { DNSView } from '../../components/DNSView';
import { CAView } from '../../components/CAView';
import { EnclavesPage } from '../enclaves/EnclavesPage';
import { useTopologyStore } from '../../stores/topologyStore';
import { useWebSocket } from '../../hooks/useWebSocket';
import { useTerrainEvents } from '../../hooks/useTerrainEvents';
import { APIError, apiService } from '../../services/api';
import { getISPColor, getLinkColor } from '../../utils/colors';
import { fallbackLayer } from '../../utils/mapLayer';
import { Link } from '../../types';
import { FeatureCollection } from '../../types/slices';
import { Section } from '../../shell/sections';
import { canIn } from '../../auth/AuthProvider';
import { Deployment } from '../../types/control';
import { JobsPage } from '../jobs/JobsPage';
import { OverviewPage } from '../overview/OverviewPage';
import { DeploymentSettings } from '../settings/DeploymentSettings';
import { lazyNamed } from '../../utils/lazy';
import '../../App.css';

// Chunks of their own: the map with Leaflet, the builder with yaml, the console with xterm
const MapView = lazyNamed(() => import('../../components/MapView'), 'MapView');
const BuildWizard = lazyNamed(() => import('../builder/BuildWizard'), 'BuildWizard');
const ConsoleModal = lazyNamed(() => import('../../components/ConsoleModal'), 'ConsoleModal');
const DesktopModal = lazyNamed(() => import('../../components/DesktopModal'), 'DesktopModal');

// The views' names before routes, which the moved code still uses
type AppTab = 'map' | 'dns' | 'ca' | 'instances';
const TAB_SECTION: Record<AppTab, Section> = { map: 'map', dns: 'dns', ca: 'certificates', instances: 'enclaves' };
const SECTION_TAB: Partial<Record<Section, AppTab>> = { map: 'map', dns: 'dns', certificates: 'ca', enclaves: 'instances' };

export const Workspace: React.FC<{ deployment: Deployment; section: Section; item?: string }> = ({ deployment, section, item }) => {
  const deploymentId = deployment.id;
  const {
    selectedRouter,
    loading,
    error,
    setRouters,
    setLinks,
    setISPs,
    setDNSServers,
    setCAServers,
    setSelectedRouter,
    setLoading,
    setError,
    setMapLayer,
  } = useTopologyStore();

  useWebSocket();
  // The map's chunk is fetched now, so it is there when the map first opens (and can center at once)
  useEffect(() => { MapView.preload(); }, []);
  // The map mounts the first time it is shown, then stays mounted (hidden) to keep Leaflet's state
  const [mapShown, setMapShown] = useState(false);
  const [selectedLink, setSelectedLink] = useState<Link | null>(null);
  const [presetModalOpen, setPresetModalOpen] = useState(false);
  // The active base preset (null: none) and how many changes were made since it
  const [presetName, setPresetName] = useState<string | null>(null);
  const [changesSince, setChangesSince] = useState(0);
  const [mapCenterOn, setMapCenterOn] = useState<{ lat: number; lng: number } | null>(null);
  const navigate = useNavigate();
  const activeTab = SECTION_TAB[section] ?? null;
  if (activeTab === 'map' && !mapShown) setMapShown(true);
  const setActiveTab = useCallback((tab: AppTab) => navigate(`/d/${deploymentId}/${TAB_SECTION[tab]}`), [navigate, deploymentId]);
  const [consoleHostname, setConsoleHostname] = useState<string | null>(null);
  const [desktopHostname, setDesktopHostname] = useState<string | null>(null);
  const [mapBanner, setMapBanner] = useState<string | null>(null);
  const [selectedInstance, setSelectedInstance] = useState<string | null>(null);

  // The map layer (GET /api/v1/map), or the one already requested. Without it (no token yet, or
  // none configured) the map is built from the topology routes, with a banner saying why.
  const loadMapLayer = useCallback(async (requested?: Promise<FeatureCollection>) => {
    try {
      setMapLayer(await (requested ?? apiService.getMap()));
      setMapBanner(null);
    } catch (err) {
      const { routers: r, links: l } = useTopologyStore.getState();
      setMapLayer(fallbackLayer(r, l, getISPColor, getLinkColor));
      if (err instanceof APIError && err.status === 503) {
        setMapBanner(`Showing the plain map: ${err.message}`);
      } else {
        setMapBanner(`Showing the plain map: the map layer failed (${err instanceof Error ? err.message : String(err)})`);
      }
    }
  }, [setMapLayer]);

  useTerrainEvents(true, loadMapLayer);

  // The active preset and the changes since it, for the map's preset box
  const checkChanges = useCallback(async () => {
    try {
      const { preset, changes } = await apiService.getChanges();
      setPresetName(preset);
      setChangesSince(changes.length);
    } catch {
      // Silently ignore — not critical
    }
  }, []);
  // Changes come from the map's panels and the other views: look again now and then while the map is open
  useEffect(() => {
    if (activeTab !== 'map') return;
    const timer = setInterval(checkChanges, 15000);
    return () => clearInterval(timer);
  }, [activeTab, checkChanges]);

  // Load topology data on mount
  useEffect(() => {
    const loadTopology = async () => {
      setLoading(true);
      setError(null);

      try {
        // The map layer is asked for with the topology; its fallback needs the topology, so it is set after
        const layer = apiService.getMap();
        layer.catch(() => {});
        // Load all topology data in parallel
        const [routersData, linksData, ispsData, dnsData, caData] = await Promise.all([
          apiService.getRouters(),
          apiService.getLinks(),
          apiService.getISPs(),
          apiService.getDNSServers(),
          apiService.getCAServers(),
        ]);

        setRouters(routersData);
        setLinks(linksData);
        setISPs(ispsData);
        setDNSServers(dnsData);
        setCAServers(caData);

        console.log(`Loaded ${routersData.length} routers, ${linksData.length} links, ${ispsData.length} ISPs, ${dnsData.length} DNS servers, ${caData.length} CAs`);

        await loadMapLayer(layer);

        // The active preset and changes since, after topology loads
        await checkChanges();
      } catch (err) {
        console.error('Error loading topology:', err);
        setError(err instanceof Error ? err.message : 'Failed to load topology');
      } finally {
        setLoading(false);
      }
    };

    loadTopology();
  }, [setRouters, setLinks, setISPs, setDNSServers, setCAServers, setLoading, setError, loadMapLayer, checkChanges]);

  // Handle router selection
  const handleRouterSelect = async (hostname: string) => {
    setSelectedLink(null); // Close link panel when selecting router
    try {
      const detail = await apiService.getRouterDetail(hostname);
      setSelectedRouter(detail);
    } catch (err) {
      console.error('Error loading router detail:', err);
    }
  };

  // Handle link selection
  const handleLinkSelect = (link: Link) => {
    setSelectedRouter(null); // Close router panel when selecting link
    setSelectedLink(link);
  };

  // Close detail panel
  const handleClosePanel = () => {
    setSelectedRouter(null);
  };

  // Close link panel
  const handleCloseLinkPanel = () => {
    setSelectedLink(null);
  };

  // Clear all selections when clicking empty map space
  const handleMapClick = () => {
    setSelectedRouter(null);
    setSelectedLink(null);
  };

  // Handle router selection from dropdown (centers map and opens panel)
  const handleRouterDropdownSelect = async (hostname: string, lat: number, lng: number) => {
    setActiveTab('map');
    setMapCenterOn({ lat, lng });
    // Clear center after animation starts to allow re-centering on same location
    setTimeout(() => setMapCenterOn(null), 100);
    await handleRouterSelect(hostname);
  };

  // Handle console open
  const handleOpenConsole = (hostname: string) => {
    setConsoleHostname(hostname);
  };

  // Handle desktop open
  const handleOpenDesktop = (hostname: string) => {
    setDesktopHostname(hostname);
  };

  if (loading) {
    return <div className="gs-empty">Loading {deploymentId}…</div>;
  }

  if (error) {
    return <div className="gs-page"><div className="gs-banner bad">Couldn't load {deploymentId}: {error}</div></div>;
  }

  return (
    <div className="gs-workspace">

      {/* Map sub-bar */}
      {activeTab === 'map' && (
        <div className="map-subbar">
          <div className="map-subbar-left">
            <span className="subbar-preset">
              Preset: <button
                className="dropdown-trigger"
                onClick={() => setPresetModalOpen(true)}
                title={changesSince ? `${changesSince} change${changesSince === 1 ? '' : 's'} since ${presetName ?? 'the core'}, not in a preset` : 'Presets: saved sets of changes to the core'}
              >{presetName ?? 'None'}{changesSince > 0 && <span className="unsaved-indicator">*</span>}</button>
            </span>
            <ISPDropdown />
            <RouterDropdown onRouterSelect={handleRouterDropdownSelect} />
          </div>
          <div className="map-subbar-right">
          </div>
        </div>
      )}

      {/* Main content */}
      <div className="main-content">
        {/* Map tab - use display:none to preserve Leaflet state */}
        <div className="map-container" style={{ display: activeTab === 'map' ? undefined : 'none', position: 'relative' }}>
          {mapBanner && <div className="map-banner">{mapBanner}</div>}
          {mapShown && <Suspense fallback={<div className="gs-empty">Loading the map…</div>}><MapView
            visible={activeTab === 'map'}
            onRouterSelect={handleRouterSelect}
            onLinkSelect={handleLinkSelect}
            onMapClick={handleMapClick}
            centerOn={mapCenterOn}
            selectedRouterHostname={selectedRouter?.hostname ?? null}
            onOpenConsole={handleOpenConsole}
          /></Suspense>}
        </div>

        {/* Router detail panel (map tab only) */}
        {activeTab === 'map' && selectedRouter && (
          <ResizablePanel defaultWidth={400} minWidth={350} maxWidth={900}>
            <RouterDetailPanel
              router={selectedRouter}
              onClose={handleClosePanel}
              onOpenConsole={handleOpenConsole}
            />
          </ResizablePanel>
        )}

        {/* Link traffic panel (map tab only) */}
        {activeTab === 'map' && selectedLink && (
          <ResizablePanel defaultWidth={400} minWidth={350} maxWidth={700}>
            <LinkTrafficPanel link={selectedLink} onClose={handleCloseLinkPanel} />
          </ResizablePanel>
        )}

        {section === 'overview' && <OverviewPage deployment={deployment} />}
        {section === 'build' && <Suspense fallback={<div className="gs-empty">Loading…</div>}><BuildWizard deployment={deployment} step={item} /></Suspense>}
        {section === 'settings' && <DeploymentSettings deployment={deployment} />}
        {section === 'jobs' && <JobsPage deploymentId={deploymentId} jobId={item} canBuild={canIn(deployment, 'build')} />}

        {/* DNS tab */}
        {activeTab === 'dns' && (
          <DNSView onOpenConsole={handleOpenConsole} />
        )}

        {/* CAs tab */}
        {activeTab === 'ca' && (
          <CAView onOpenConsole={handleOpenConsole} />
        )}



        {/* Instances tab */}
        {activeTab === 'instances' && (
          <EnclavesPage
            deployment={deployment}
            selectedId={selectedInstance}
            onSelect={setSelectedInstance}
            onOpenConsole={handleOpenConsole}
            onOpenDesktop={handleOpenDesktop}
            onShowOnMap={(lat, lng) => {
              setActiveTab('map');
              loadMapLayer();
              setMapCenterOn({ lat, lng });
              setTimeout(() => setMapCenterOn(null), 100);
            }}
          />
        )}
      </div>

      {/* Preset Modal */}
      {presetModalOpen && (
        <PresetModal
          onClose={() => { setPresetModalOpen(false); checkChanges(); }}
          onChanged={() => { checkChanges(); loadMapLayer(); }}
          canOperate={canIn(deployment, 'operate')}
          canBuild={canIn(deployment, 'build')}
        />
      )}

      {/* Console Modal */}
      {consoleHostname && <Suspense fallback={null}><ConsoleModal hostname={consoleHostname} onClose={() => setConsoleHostname(null)} /></Suspense>}

      {/* Desktop Modal */}
      {desktopHostname && <Suspense fallback={null}><DesktopModal hostname={desktopHostname} onClose={() => setDesktopHostname(null)} /></Suspense>}
    </div>
  );
};
