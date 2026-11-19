/**
 * Zustand store for topology and network state management
 */
import { create } from 'zustand';
import { Router, RouterDetail, Link, ISP, LinkState, LinkStateUpdate, DNSServer, CAServer } from '../types';
import { FeatureCollection } from '../types/slices';
import { applyFeatureUpdate } from '../utils/mapLayer';
import { getISPColor } from '../utils/colors';

interface TopologyState {
  // Data
  routers: Router[];
  links: Link[];
  isps: ISP[];
  dnsServers: DNSServer[];
  caServers: CAServer[];
  selectedRouter: RouterDetail | null;
  linkStates: Map<string, LinkState>;
  hiddenISPs: Set<string>;
  // The map layer (GET /api/v1/map), what MapView draws
  mapLayer: FeatureCollection | null;

  // Loading states
  loading: boolean;
  error: string | null;

  // Actions
  setRouters: (routers: Router[]) => void;
  setLinks: (links: Link[]) => void;
  setISPs: (isps: ISP[]) => void;
  setDNSServers: (servers: DNSServer[]) => void;
  setCAServers: (servers: CAServer[]) => void;
  setSelectedRouter: (router: RouterDetail | null) => void;
  updateLinkState: (update: LinkStateUpdate) => void;
  setInitialLinkStates: (states: Array<{ link_id: string; state: string; diagnostics: string[] }>) => void;
  setLoading: (loading: boolean) => void;
  setMapLayer: (layer: FeatureCollection) => void;
  updateFeature: (id: string, properties: Record<string, unknown>) => void;
  setError: (error: string | null) => void;
  toggleISPVisibility: (ispName: string) => void;
  showAllISPs: () => void;
  hideAllISPs: () => void;
  // Forget everything: another deployment opens
  reset: () => void;

  // Utility functions
  getLinkById: (id: string) => Link | undefined;
  getLinkByRouters: (source: string, target: string) => Link | undefined;
  getRouterByHostname: (hostname: string) => Router | undefined;
  getRouterAttachedItems: (hostname: string) => Array<DNSServer | CAServer>;
  // An ISP's color as the map layer assigns it (the base's ISP order), so legends match the map
  ispColor: (isp: string) => string;
}

export const useTopologyStore = create<TopologyState>((set, get) => ({
  // Initial state
  routers: [],
  links: [],
  isps: [],
  dnsServers: [],
  caServers: [],
  selectedRouter: null,
  linkStates: new Map(),
  hiddenISPs: new Set(),
  mapLayer: null,
  loading: false,
  error: null,

  // Actions
  reset: () => set({
    routers: [], links: [], isps: [], dnsServers: [], caServers: [], selectedRouter: null,
    linkStates: new Map(), hiddenISPs: new Set(), mapLayer: null, loading: false, error: null,
  }),
  setRouters: (routers) => set({ routers }),

  setLinks: (links) => set({ links }),

  setISPs: (isps) => set({ isps }),

  setDNSServers: (dnsServers) => set({ dnsServers }),

  setCAServers: (caServers) => set({ caServers }),

  setSelectedRouter: (router) => set({ selectedRouter: router }),

  setMapLayer: (mapLayer) => set({ mapLayer }),

  updateFeature: (id, properties) => set({ mapLayer: applyFeatureUpdate(get().mapLayer, id, properties) }),

  updateLinkState: (update: LinkStateUpdate) => {
    const { linkStates } = get();
    const newStates = new Map(linkStates);

    newStates.set(update.link_id, {
      state: update.state,
      diagnostics: update.diagnostics,
      timestamp: update.timestamp,
    });

    set({ linkStates: newStates });
  },

  setInitialLinkStates: (states) => {
    const newStates = new Map<string, LinkState>();
    for (const entry of states) {
      newStates.set(entry.link_id, {
        state: entry.state as LinkState['state'],
        diagnostics: entry.diagnostics,
      });
    }
    set({ linkStates: newStates });
  },

  setLoading: (loading) => set({ loading }),

  setError: (error) => set({ error }),

  toggleISPVisibility: (ispName: string) => {
    const { hiddenISPs } = get();
    const newHidden = new Set(hiddenISPs);
    if (newHidden.has(ispName)) {
      newHidden.delete(ispName);
    } else {
      newHidden.add(ispName);
    }
    set({ hiddenISPs: newHidden });
  },

  showAllISPs: () => {
    set({ hiddenISPs: new Set() });
  },

  hideAllISPs: () => {
    const { isps } = get();
    set({ hiddenISPs: new Set(isps.map((isp) => isp.name)) });
  },

  // Utility functions
  getLinkById: (id: string) => {
    return get().links.find((link) => link.id === id);
  },

  getLinkByRouters: (source: string, target: string) => {
    return get().links.find(
      (link) =>
        (link.source_hostname === source && link.target_hostname === target) ||
        (link.source_hostname === target && link.target_hostname === source)
    );
  },

  getRouterByHostname: (hostname: string) => {
    return get().routers.find((router) => router.hostname === hostname);
  },

  getRouterAttachedItems: (hostname: string) => {
    const { dnsServers, caServers } = get();
    const items: Array<DNSServer | CAServer> = [];
    for (const dns of dnsServers) {
      if (dns.router === hostname) items.push(dns);
    }
    for (const ca of caServers) {
      if (ca.router === hostname) items.push(ca);
    }
    return items;
  },

  ispColor: (isp: string) => {
    const feature = get().mapLayer?.features.find((f) => f.properties.kind === 'router' && f.properties.isp === isp);
    return feature?.properties.color || getISPColor(isp);
  },
}));
