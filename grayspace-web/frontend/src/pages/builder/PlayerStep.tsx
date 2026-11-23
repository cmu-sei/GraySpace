/**
 * Step: the deployment's Crucible Player view (the draft's player section; only with the
 * deployment's Player option). The view and its applications, the teams with the applications
 * each gets, and which resources each team sees: the base's, grouped, and the chosen enclaves'
 * sites and hosts. The Base row and each enclave's row are rules (the draft's assign): every
 * resource there, now and later, goes to the team. A resource whose teams differ is listed in
 * guests. A host or site the enclave spec marks crucible: true also goes to the default team.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { apiService } from '../../services/api';
import { DraftPlayer, PlayerApplication, PlayerCandidates, PlayerGuest, PlayerRole, PlayerRule, PlayerTeam, PlayerTemplate } from '../../types/build';
import { Tip } from '../../components/Tip';
import { Builder, newPlayer } from './useBuilder';

interface Row {
  key: string;
  instance: string | null;
  name: string;
  site: boolean;
  label: string;
  detail?: string;
  hint: boolean;
}

interface Group {
  key: string;
  label: string;
  rows: Row[];
  /** An enclave's group: its row is the enclave's rule */
  scope?: string;
}

const scopeOf = (r: { instance?: string | null }) => (r.instance ? `enclave:${r.instance}` : 'base');
const sameTeams = (a: string[], b: string[]) => a.length === b.length && a.every((t) => b.includes(t));

const rowKey = (instance: string | null | undefined, name: string, site = false) => `${instance ?? ''}|${site ? 'site' : 'host'}|${name}`;

const GROUP_LABEL: Record<string, string> = { routers: 'Routers', core_services: 'Core services', management: 'Management' };

/** The candidates as groups: the base's by kind and ISP, then each enclave's sites and hosts. */
const groupsOf = (c: PlayerCandidates | null): Group[] => {
  if (!c) return [];
  const out: Group[] = [];
  const byKey: Record<string, Group> = {};
  for (const g of c.base) {
    const split = g.group === 'routers';
    const key = `base:${g.group}:${split ? g.isp ?? '' : ''}`;
    if (!byKey[key]) {
      byKey[key] = { key, label: `${GROUP_LABEL[g.group]}${split ? ` · ${g.isp ?? 'no ISP'}` : ''}`, rows: [] };
      out.push(byKey[key]);
    }
    byKey[key].rows.push({ key: rowKey(null, g.hostname), instance: null, name: g.hostname, site: false, label: g.hostname,
      detail: g.role ?? g.type ?? (g.group === 'core_services' ? g.isp ?? undefined : undefined), hint: false });
  }
  for (const e of c.enclaves) {
    out.push({
      key: `enclave:${e.prefix}`, label: `${e.slug} · ${e.prefix}`, scope: `enclave:${e.prefix}`,
      rows: [
        ...e.sites.map((s) => ({ key: rowKey(e.prefix, s.name, true), instance: e.prefix, name: s.name, site: true, label: `${s.name} (site)`, detail: s.edge, hint: s.hint })),
        ...e.hosts.map((h) => ({ key: rowKey(e.prefix, h.name), instance: e.prefix, name: h.name, site: false, label: h.count > 1 ? `${h.name} ×${h.count}` : h.name,
          detail: h.image, hint: h.hint })),
      ],
    });
  }
  return out;
};

export const PlayerStep: React.FC<{ b: Builder; readOnly: boolean }> = ({ b, readOnly }) => {
  const p = b.player;
  const [candidates, setCandidates] = useState<PlayerCandidates | null>(null);
  const [templates, setTemplates] = useState<PlayerTemplate[]>([]);
  const [roles, setRoles] = useState<PlayerRole[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    apiService.getPlayerTemplates().then(setTemplates).catch((err) => setError(`Player's application templates: ${err.message}`));
    apiService.getPlayerRoles().then(setRoles).catch(() => setRoles([]));
  }, []);
  useEffect(() => {
    apiService.getPlayerGuests(b.base?.version, b.base?.scope).then(setCandidates).catch((err) => setError(err.message));
  }, [b.base?.version, b.base?.scope]);

  const groups = useMemo(() => groupsOf(candidates), [candidates]);
  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return f ? groups.map((g) => ({ ...g, rows: g.rows.filter((r) => `${r.label} ${r.detail ?? ''} ${g.label}`.toLowerCase().includes(f)) })).filter((g) => g.rows.length) : groups;
  }, [groups, filter]);

  if (!p) {
    return (
      <div className="gs-box">
        <h3>Player view</h3>
        <div className="gs-muted" style={{ marginBottom: 8 }}>No Player view.</div>
        <button className="gs-btn" disabled={readOnly} onClick={() => b.setPlayer(newPlayer())}>Add a Player view</button>
      </div>
    );
  }

  const set = (patch: Partial<DraftPlayer>) => b.setPlayer({ ...p, ...patch });
  const teamNames = p.teams.map((t) => t.name);
  const defaultTeam = p.default_team || teamNames[0];
  const assign = p.assign ?? [];
  const explicit = new Map(p.guests.map((g) => [rowKey(g.instance, g.name, g.site), g]));
  const rows = new Map(groups.flatMap((g) => g.rows).map((r) => [r.key, r]));
  const ruleOf = (scope: string, rules: PlayerRule[] = assign) => rules.find((r) => r.scope === scope)?.teams ?? [];
  // What a resource gets without an entry of its own: its part's rule, and the default team if its spec hints it
  const implied = (r: Row, rules: PlayerRule[] = assign) => {
    const teams = ruleOf(scopeOf(r), rules);
    return r.hint && defaultTeam && !teams.includes(defaultTeam) ? [...teams, defaultTeam] : teams;
  };
  const teamsOf = (r: Row): string[] => explicit.get(r.key)?.teams ?? implied(r);
  const entry = (r: Row, teams: string[]): PlayerGuest => ({ name: r.name, teams, ...(r.instance ? { instance: r.instance } : {}), ...(r.site ? { site: true } : {}) });
  // Only the entries that differ from what the rules give, so a rule reaches everything else; and none
  // for a base guest the base no longer has (nobody could untick it here)
  const tidy = (entries: PlayerGuest[], rules: PlayerRule[]) => entries.filter((g) => {
    const r = rows.get(rowKey(g.instance, g.name, g.site));
    if (!r) return Boolean(g.instance) || !candidates?.base.length;
    return !sameTeams(g.teams, implied(r, rules));
  });
  const setTeams = (list: Row[], team: string, on: boolean) => {
    const changed = new Map(explicit);
    for (const r of list) {
      const now = teamsOf(r);
      changed.set(r.key, entry(r, on ? Array.from(new Set([...now, team])) : now.filter((t) => t !== team)));
    }
    set({ guests: tidy(Array.from(changed.values()), assign) });
  };
  /** Every resource of the scopes to the team, or none: their rules, and the entries in them follow */
  const setRule = (scopes: string[], team: string, on: boolean) => {
    const rules = [...assign];
    for (const scope of scopes) {
      const i = rules.findIndex((r) => r.scope === scope);
      const now = i >= 0 ? rules[i].teams : [];
      const teams = on ? Array.from(new Set([...now, team])) : now.filter((t) => t !== team);
      if (i >= 0) rules[i] = { scope, teams }; else rules.push({ scope, teams });
    }
    const next = rules.filter((r) => r.teams.length);
    const guests = p.guests.map((g) => (scopes.includes(scopeOf(g))
      ? { ...g, teams: on ? Array.from(new Set([...g.teams, team])) : g.teams.filter((t) => t !== team) } : g));
    set({ assign: next, guests: tidy(guests, next) });
  };

  const setApp = (i: number, patch: Partial<PlayerApplication>) => {
    const old = p.applications[i];
    const apps = p.applications.map((a, j) => (j === i ? { ...a, ...patch } : a));
    const teams = patch.name !== undefined && patch.name !== old.name
      ? p.teams.map((t) => ({ ...t, applications: t.applications.map((a) => (a === old.name ? patch.name! : a)) })) : p.teams;
    set({ applications: apps, teams });
  };
  const removeApp = (i: number) => {
    const name = p.applications[i].name;
    set({ applications: p.applications.filter((_, j) => j !== i), teams: p.teams.map((t) => ({ ...t, applications: t.applications.filter((a) => a !== name) })) });
  };
  const addTemplate = (id: string) => {
    const t = templates.find((x) => x.id === id);
    if (!t) return;
    const taken = new Set(p.applications.map((a) => a.name));
    let name = t.name;
    for (let n = 2; taken.has(name); n++) name = `${t.name} ${n}`;
    // A new application goes to every team; untick it where it doesn't belong
    set({ applications: [...p.applications, { name, template_id: t.id, embeddable: t.embeddable ?? true }], teams: p.teams.map((tm) => ({ ...tm, applications: [...tm.applications, name] })) });
  };
  const setTeam = (i: number, patch: Partial<PlayerTeam>) => {
    const old = p.teams[i];
    const teams = p.teams.map((t, j) => (j === i ? { ...t, ...patch } : t));
    if (patch.name !== undefined && patch.name !== old.name) {
      const rename = (list: string[]) => list.map((t) => (t === old.name ? patch.name! : t));
      set({ teams, default_team: p.default_team === old.name ? patch.name : p.default_team,
        assign: assign.map((r) => ({ ...r, teams: rename(r.teams) })), guests: p.guests.map((g) => ({ ...g, teams: rename(g.teams) })) });
    } else {
      set({ teams });
    }
  };
  const removeTeam = (i: number) => {
    const name = p.teams[i].name;
    const rules = assign.map((r) => ({ ...r, teams: r.teams.filter((t) => t !== name) })).filter((r) => r.teams.length);
    set({ teams: p.teams.filter((_, j) => j !== i), default_team: p.default_team === name ? null : p.default_team,
      assign: rules, guests: tidy(p.guests.map((g) => ({ ...g, teams: g.teams.filter((t) => t !== name) })), rules) });
  };
  const moveApp = (team: number, app: string, by: number) => {
    const list = [...p.teams[team].applications];
    const i = list.indexOf(app);
    const j = i + by;
    if (i < 0 || j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    setTeam(team, { applications: list });
  };
  const sections = [
    { key: 'base', label: 'Core', groups: shown.filter((g) => !g.scope), scopes: ['base'] },
    { key: 'enclaves', label: 'Enclaves', groups: shown.filter((g) => g.scope), scopes: groups.filter((g) => g.scope).map((g) => g.scope!) },
  ].filter((sec) => sec.groups.length);
  // A rule's box: ticked when every scope's rule has the team; half when some resources have it anyway
  const ruleBox = (label: string, scopes: string[], list: Row[], t: string) => {
    const on = scopes.length > 0 && scopes.every((sc) => ruleOf(sc).includes(t));
    const some = !on && list.some((r) => teamsOf(r).includes(t));
    return (
      <td key={t} style={{ textAlign: 'center' }}>
        <input type="checkbox" disabled={readOnly} aria-label={label} checked={on}
          ref={(el) => { if (el) el.indeterminate = some; }} onChange={(e) => setRule(scopes, t, e.target.checked)} />
      </td>
    );
  };
  const counts = teamNames.map((t) => groups.reduce((n, g) => n + g.rows.filter((r) => teamsOf(r).includes(t)).length, 0));
  const duplicateTeams = teamNames.filter((t, i) => teamNames.indexOf(t) !== i);

  return (
    <div>
      {error && <div className="gs-banner warn">{error}</div>}
      <div className="gs-grid3" style={{ alignItems: 'start' }}>
        <div className="gs-box">
          <h3>View</h3>
          <div className="gs-field"><label htmlFor="pv-name">Name</label>
            <input id="pv-name" disabled={readOnly} value={p.view_name ?? ''} placeholder="<deployment>-grayspace" onChange={(e) => set({ view_name: e.target.value || null })} /></div>
          <div className="gs-field"><label htmlFor="pv-description">Description</label>
            <input id="pv-description" disabled={readOnly} value={p.description ?? ''} onChange={(e) => set({ description: e.target.value })} /></div>
          <button className="gs-btn ghost sm" style={{ marginTop: 8 }} disabled={readOnly}
            onClick={() => window.confirm('Remove the Player view from the draft? Deploying then removes the view.') && b.setPlayer(null)}>Remove the view</button>
        </div>

        <div className="gs-box">
          <h3>Applications</h3>
          {p.applications.length === 0 && <div className="gs-muted">No applications yet. Add VMs to show the resources.</div>}
          {p.applications.map((a, i) => (
            <div key={i} className="gs-row" style={{ marginBottom: 6, flexWrap: 'wrap' }}>
              <input className="gs-input" style={{ width: 120 }} aria-label="Application name" disabled={readOnly} value={a.name} onChange={(e) => setApp(i, { name: e.target.value })} />
              {a.template_id ? <span className="gs-tag" title={a.template_id}>{templates.find((t) => t.id === a.template_id)?.name ?? 'template'}</span>
                : <input className="gs-input" style={{ flex: 1 }} aria-label={`${a.name} URL`} disabled={readOnly} value={a.url ?? ''} placeholder="https://…" onChange={(e) => setApp(i, { url: e.target.value })} />}
              <label className="gs-row" title="Shown inside Player rather than in a new tab"><input type="checkbox" disabled={readOnly} checked={a.embeddable ?? true} onChange={(e) => setApp(i, { embeddable: e.target.checked })} />embed</label>
              <button className="gs-btn ghost sm" disabled={readOnly} aria-label={`Remove ${a.name}`} onClick={() => removeApp(i)}>✕</button>
            </div>
          ))}
          <div className="gs-row" style={{ marginTop: 6 }}>
            <select className="gs-input" aria-label="Add an application" disabled={readOnly || !templates.length} value="" onChange={(e) => addTemplate(e.target.value)}>
              <option value="">{templates.length ? 'Add from Player…' : 'No templates'}</option>
              {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <button className="gs-btn ghost sm" disabled={readOnly}
              onClick={() => set({ applications: [...p.applications, { name: `App ${p.applications.length + 1}`, url: '', embeddable: true }] })}>＋ URL</button>
          </div>
        </div>

        <div className="gs-box">
          <h3>Teams <Tip text="People join teams in Player." /><span className="gs-spacer" />
            <button className="gs-btn ghost sm" disabled={readOnly} onClick={() => {
              let name = 'Red';
              for (let n = 2; teamNames.includes(name); n++) name = `Team ${n}`;
              set({ teams: [...p.teams, { name, role: roles[0]?.name ?? 'View Member', applications: p.applications.map((a) => a.name) }] });
            }}>＋ Team</button></h3>
          {p.teams.map((t, i) => (
            <div key={i} className="gs-box" style={{ background: 'transparent', padding: 8 }}>
              <div className="gs-row">
                <input className="gs-input" style={{ width: 110 }} aria-label="Team name" disabled={readOnly} value={t.name} onChange={(e) => setTeam(i, { name: e.target.value })} />
                <select className="gs-input" style={{ width: 'auto' }} aria-label={`${t.name} role`} disabled={readOnly} value={t.role ?? ''} onChange={(e) => setTeam(i, { role: e.target.value || null })}>
                  <option value="">Player's default</option>
                  {Array.from(new Set([...roles.map((r) => r.name), ...(t.role ? [t.role] : [])])).map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <label className="gs-row" title="Gets the hosts and sites an enclave marks for Crucible"><input type="radio" name="default-team" disabled={readOnly}
                  checked={defaultTeam === t.name} onChange={() => set({ default_team: t.name })} />default</label>
                <span className="gs-spacer" />
                <button className="gs-btn ghost sm" disabled={readOnly || p.teams.length === 1} aria-label={`Remove ${t.name}`} onClick={() => removeTeam(i)}>✕</button>
              </div>
              <div style={{ marginTop: 6 }}>
                {p.applications.map((a) => {
                  const on = t.applications.includes(a.name);
                  return (
                    <div key={a.name} className="gs-row" style={{ fontSize: 12 }}>
                      <label className="gs-row"><input type="checkbox" disabled={readOnly} checked={on} aria-label={`${t.name} gets ${a.name}`}
                        onChange={(e) => setTeam(i, { applications: e.target.checked ? [...t.applications, a.name] : t.applications.filter((x) => x !== a.name) })} />{a.name}</label>
                      {on && <>
                        <span className="gs-muted">#{t.applications.indexOf(a.name) + 1}</span>
                        <button className="gs-btn ghost sm" disabled={readOnly} aria-label={`${a.name} up for ${t.name}`} onClick={() => moveApp(i, a.name, -1)}>↑</button>
                        <button className="gs-btn ghost sm" disabled={readOnly} aria-label={`${a.name} down for ${t.name}`} onClick={() => moveApp(i, a.name, 1)}>↓</button>
                      </>}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          {duplicateTeams.length > 0 && <div className="gs-finding bad">Two teams are called {duplicateTeams[0]}.</div>}
        </div>
      </div>

      <div className="gs-box">
        <h3>Who sees which resources <span className="gs-spacer" />
          <input className="gs-input" style={{ width: 220 }} placeholder="Filter: hostname, ISP, enclave" aria-label="Filter resources" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </h3>
        {!candidates ? <div className="gs-muted">Loading…</div> : (
          <table className="gs-table">
            <thead><tr><th>Resource</th>{teamNames.map((t, i) => <th key={t} style={{ textAlign: 'center' }}>{t}<div className="gs-muted" style={{ fontWeight: 400 }}>{counts[i]}</div></th>)}</tr></thead>
            <tbody>
              {sections.map((sec) => {
                const secRows = sec.groups.flatMap((g) => g.rows);
                const secOpen = open[sec.key] ?? true;
                return (
                  <React.Fragment key={sec.key}>
                    <tr className="gs-section-row">
                      <td><button className="gs-btn ghost sm" aria-expanded={secOpen} onClick={() => setOpen({ ...open, [sec.key]: !secOpen })}>{secOpen ? '▾' : '▸'}</button>{' '}
                        <b>{sec.label}</b> <span className="gs-muted">{secRows.length}</span>
                        <Tip text={sec.key === 'base' ? 'Ticking a team here gives it every core resource.' : 'Ticking a team here gives it every enclave, and the ones added later.'} /></td>
                      {teamNames.map((t) => ruleBox(`${sec.label}: all to ${t}`, sec.scopes, secRows, t))}
                    </tr>
                    {secOpen && sec.groups.map((g) => {
                      const expanded = open[g.key] ?? (Boolean(filter) || Boolean(g.scope));
                      return (
                        <React.Fragment key={g.key}>
                          <tr>
                            <td style={{ paddingLeft: 24 }}><button className="gs-btn ghost sm" aria-expanded={expanded} onClick={() => setOpen({ ...open, [g.key]: !expanded })}>{expanded ? '▾' : '▸'}</button>{' '}
                              <b>{g.label}</b> <span className="gs-muted">{g.rows.length}</span></td>
                            {teamNames.map((t) => (g.scope ? ruleBox(`${g.label}: all to ${t}`, [g.scope], g.rows, t) : (() => {
                              const n = g.rows.filter((r) => teamsOf(r).includes(t)).length;
                              return (
                                <td key={t} style={{ textAlign: 'center' }}>
                                  <input type="checkbox" disabled={readOnly} aria-label={`${g.label}: all to ${t}`} checked={n === g.rows.length && n > 0}
                                    ref={(el) => { if (el) el.indeterminate = n > 0 && n < g.rows.length; }} onChange={(e) => setTeams(g.rows, t, e.target.checked)} />
                                </td>
                              );
                            })()))}
                          </tr>
                          {expanded && g.rows.map((r) => {
                            const teams = teamsOf(r);
                            const own = explicit.has(r.key);
                            return (
                              <tr key={r.key}>
                                <td style={{ paddingLeft: 58 }}>
                                  <span className="gs-mono">{r.label}</span>
                                  {r.detail && <span className="gs-muted" style={{ marginLeft: 6, fontSize: 11 }}>{r.detail}</span>}
                                  {r.hint && <span className="gs-pill info" style={{ marginLeft: 6 }} title="The enclave spec marks it for Crucible">{own ? 'spec hint' : 'from the spec'}</span>}
                                  {own && <span className="gs-pill" style={{ marginLeft: 6 }} title="Its teams differ from the row above">own teams</span>}
                                </td>
                                {teamNames.map((t) => (
                                  <td key={t} style={{ textAlign: 'center' }}>
                                    <input type="checkbox" disabled={readOnly} aria-label={`${r.label} in ${t}`} checked={teams.includes(t)} onChange={(e) => setTeams([r], t, e.target.checked)} />
                                  </td>
                                ))}
                              </tr>
                            );
                          })}
                        </React.Fragment>
                      );
                    })}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};
