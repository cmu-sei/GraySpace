/**
 * The builder's state: the draft (base and enclave instances), the enclave library with each
 * enclave's latest spec, what the deployment's base can offer (capabilities), and each selected
 * enclave's findings against the draft's base, and the Player view (with the deployment's Player
 * option). Nothing is stored until save(): edited specs (each a new version, which the draft then
 * pins) and then the draft.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiService } from '../../services/api';
import { BaseVersion, Capabilities, DraftBase, DraftEnclave, DraftPlayer, EnclaveSpec, Validation } from '../../types/build';
import { SliceSummary } from '../../types/slices';

export interface Builder {
  loading: boolean;
  error: string | null;
  versions: BaseVersion[];
  base: DraftBase | null;
  enclaves: DraftEnclave[];
  library: SliceSummary[];
  specs: Record<string, EnclaveSpec>;
  validations: Record<string, Validation>;
  capabilities: Capabilities | null;
  dirty: boolean;
  saving: boolean;
  savedAt: string | null;
  adopted: boolean;
  setBase: (base: DraftBase | null) => void;
  select: (slug: string, on: boolean, prefix?: string) => void;
  setPrefix: (slug: string, prefix: string) => void;
  /** The Player view; null: none. Only with the deployment's Player option */
  player: DraftPlayer | null;
  setPlayer: (player: DraftPlayer | null) => void;
  updateSpec: (spec: EnclaveSpec) => void;
  /** Deletes an enclave from the library (DELETE /enclaves/{slug}, refused while it has instances) and the draft */
  remove: (slug: string) => Promise<boolean>;
  save: () => Promise<boolean>;
  readOnly: (slug: string) => boolean;
}

const owned = (s: SliceSummary | undefined) => s?.owner === 'ghosts';

/** A new view: one team, no applications yet, no VMs but the ones enclave specs mark crucible: true. */
export const newPlayer = (): DraftPlayer => ({ applications: [], teams: [{ name: 'Blue', role: 'View Member', applications: [] }], guests: [] });

export const useBuilder = (canBuild: boolean, playerEnabled = false): Builder => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [versions, setVersions] = useState<BaseVersion[]>([]);
  const [base, setBaseState] = useState<DraftBase | null>(null);
  const [enclaves, setEnclaves] = useState<DraftEnclave[]>([]);
  const [library, setLibrary] = useState<SliceSummary[]>([]);
  const [specs, setSpecs] = useState<Record<string, EnclaveSpec>>({});
  const [validations, setValidations] = useState<Record<string, Validation>>({});
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [draftDirty, setDraftDirty] = useState(false);
  const [dirtySpecs, setDirtySpecs] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [adopted, setAdopted] = useState(false);
  const [player, setPlayerState] = useState<DraftPlayer | null>(null);

  useEffect(() => {
    let stopped = false;
    (async () => {
      try {
        const [draft, found, slices] = await Promise.all([apiService.getDraft(), apiService.getBaseVersions(), apiService.getSlices()]);
        const mine = slices.filter((s) => s.kind === 'slice');
        const details = await Promise.all(mine.map((s) => apiService.getSlice(s.slug).catch(() => null)));
        if (stopped) return;
        setVersions(found);
        setLibrary(mine);
        setSpecs(Object.fromEntries(details.filter((d) => d?.spec).map((d) => [d!.slug, d!.spec as unknown as EnclaveSpec])));
        const deployed = found.find((v) => v.deployed);
        setBaseState(draft.core ?? (deployed ? { version: deployed.name, scope: 'full', configure: true } : found[0] ? { version: found[0].name, scope: 'full', configure: true } : null));
        setEnclaves(draft.enclaves);
        setPlayerState(draft.player ?? (playerEnabled ? newPlayer() : null));
        setAdopted(Boolean(draft.adopted));
        setSavedAt(draft.updated ?? null);
        setDraftDirty(!draft.core && !deployed && Boolean(found[0]));
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!stopped) setLoading(false);
      }
    })();
    return () => {
      stopped = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // What the draft's base offers enclaves: ISPs and locations, images, roles, DNS servers, CAs
  useEffect(() => {
    if (!base) return;
    let stopped = false;
    apiService.getCapabilities(base.version, base.scope).then((c) => !stopped && setCapabilities(c)).catch(() => !stopped && setCapabilities(null));
    return () => {
      stopped = true;
    };
  }, [base?.version, base?.scope]); // eslint-disable-line react-hooks/exhaustive-deps

  // Each selected (or edited) enclave against the draft's base, a moment after it last changed, as
  // the draft's instance (its prefix): the instance already built from it isn't a conflict
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const prefixes = useRef<Record<string, string>>({});
  prefixes.current = Object.fromEntries(enclaves.map((e) => [e.slug, e.prefix]));
  const validate = useCallback((spec: EnclaveSpec) => {
    clearTimeout(timers.current[spec.slug]);
    setValidations((v) => ({ ...v, [spec.slug]: { ...(v[spec.slug] || { valid: true, findings: [] }), checking: true } }));
    timers.current[spec.slug] = setTimeout(async () => {
      try {
        const prefix = prefixes.current[spec.slug];
        const result = await apiService.validateSlice(spec, base, prefix && /^[a-z][a-z0-9]{0,7}$/.test(prefix) ? prefix : undefined);
        setValidations((v) => ({ ...v, [spec.slug]: { valid: result.valid, findings: result.findings } }));
      } catch (err) {
        setValidations((v) => ({ ...v, [spec.slug]: { valid: false, findings: [{ code: 'INVALID_SPEC', severity: 'error', path: '', message: err instanceof Error ? err.message : String(err) }] } }));
      }
    }, 500);
  }, [base]);

  const selectedKey = enclaves.map((e) => `${e.slug}:${e.prefix}`).join(',');
  useEffect(() => {
    enclaves.forEach((e) => specs[e.slug] && validate(specs[e.slug]));
  }, [selectedKey, validate]); // eslint-disable-line react-hooks/exhaustive-deps

  const setBase = (next: DraftBase | null) => {
    setBaseState(next);
    setDraftDirty(true);
  };

  /** prefix: the spec's default prefix, when the spec isn't in state yet (a new enclave) */
  const select = (slug: string, on: boolean, defaultPrefix?: string) => {
    setDraftDirty(true);
    setEnclaves((list) => {
      if (!on) return list.filter((e) => e.slug !== slug);
      if (list.some((e) => e.slug === slug)) return list;
      const used = new Set(list.map((e) => e.prefix));
      const wanted = defaultPrefix || specs[slug]?.prefix || slug.replace(/[^a-z0-9]/g, '').slice(0, 2) || 'e';
      let prefix = wanted;
      for (let i = 2; used.has(prefix); i++) prefix = `${wanted.slice(0, 6)}${i}`;
      return [...list, { slug, prefix, version: null }];
    });
  };

  const setPrefix = (slug: string, prefix: string) => {
    setDraftDirty(true);
    setEnclaves((list) => list.map((e) => (e.slug === slug ? { ...e, prefix } : e)));
  };

  const setPlayer = (next: DraftPlayer | null) => {
    setDraftDirty(true);
    setPlayerState(next);
  };

  const updateSpec = (spec: EnclaveSpec) => {
    setSpecs((all) => ({ ...all, [spec.slug]: spec }));
    setDirtySpecs((d) => new Set(d).add(spec.slug));
    if (!library.some((s) => s.slug === spec.slug)) {
      setLibrary((l) => [...l, { slug: spec.slug, kind: 'slice', description: spec.description || '', versions: [], instances: [], owner: null }]);
    }
    validate(spec);
  };

  const remove = async (slug: string) => {
    setError(null);
    try {
      if (library.find((s) => s.slug === slug)?.versions.length) await apiService.deleteSlice(slug);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    }
    const without = <T,>(all: Record<string, T>) => Object.fromEntries(Object.entries(all).filter(([k]) => k !== slug));
    setLibrary((l) => l.filter((s) => s.slug !== slug));
    setSpecs(without);
    setValidations(without);
    setDirtySpecs((d) => new Set(Array.from(d).filter((s) => s !== slug)));
    const entry = enclaves.find((e) => e.slug === slug);
    if (entry) {
      setEnclaves((list) => list.filter((e) => e.slug !== slug));
      // Its hosts and sites go from the Player view too
      if (player) setPlayerState({ ...player, guests: player.guests.filter((g) => g.instance !== entry.prefix) });
      setDraftDirty(true);
    }
    return true;
  };

  const save = async (): Promise<boolean> => {
    if (!canBuild) return true;
    setSaving(true);
    setError(null);
    try {
      let next = enclaves;
      for (const slug of Array.from(dirtySpecs)) {
        const stored = await apiService.putEnclave(specs[slug]);
        // The draft builds the version just saved
        next = next.map((e) => (e.slug === slug ? { ...e, version: stored.version } : e));
      }
      if (draftDirty || dirtySpecs.size) {
        const saved = await apiService.putDraft({ core: base, enclaves: next, ...(playerEnabled ? { player } : {}) });
        setEnclaves(saved.enclaves);
        setSavedAt(saved.updated ?? null);
        setAdopted(false);
      }
      setDirtySpecs(new Set());
      setDraftDirty(false);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const readOnly = useCallback((slug: string) => !canBuild || owned(library.find((s) => s.slug === slug)), [canBuild, library]);

  return useMemo(() => ({
    loading, error, versions, base, enclaves, library, specs, validations, capabilities, dirty: draftDirty || dirtySpecs.size > 0, saving,
    savedAt, adopted, setBase, select, setPrefix, player, setPlayer, updateSpec, remove, save, readOnly,
  }), [loading, error, versions, base, enclaves, library, specs, validations, capabilities, draftDirty, dirtySpecs, saving, savedAt, adopted, player, readOnly]); // eslint-disable-line react-hooks/exhaustive-deps
};
