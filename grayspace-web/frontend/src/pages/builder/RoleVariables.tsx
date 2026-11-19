/**
 * The enclave's role_vars, one group per role its hosts use. A role's variables belong to the
 * enclave, not to a host: every host with the role reads the same values, so each group names its
 * hosts. Values of roles no host uses any more are kept (shown as unused, with a × to clear them).
 * Lists and objects (mail users, a GHOSTS timeline) are edited in YAML.
 */
import React, { useEffect, useRef, useState } from 'react';
import { EnclaveSpec, Role, RoleVariable } from '../../types/build';

type Vars = Record<string, Record<string, unknown>>;

const SECRET = /password|secret|token/i;
const structured = (v: unknown) => v !== null && typeof v === 'object';

/** The spec with role_vars.<role>.<name> set, or removed when undefined (and empty groups with it). */
export const withRoleVar = (spec: EnclaveSpec, role: string, name: string, value: unknown): EnclaveSpec => {
  const vars: Vars = { ...(spec.role_vars || {}) };
  const group = { ...(vars[role] || {}) };
  if (value === undefined) delete group[name];
  else group[name] = value;
  if (Object.keys(group).length) vars[role] = group;
  else delete vars[role];
  return Object.keys(vars).length ? { ...spec, role_vars: vars } : withoutRole(spec, role);
};

/** The spec without role_vars.<role> (and without role_vars when that was the last). */
export const withoutRole = (spec: EnclaveSpec, role: string): EnclaveSpec => {
  const { [role]: _dropped, ...vars } = spec.role_vars || {};
  const { role_vars: _old, ...rest } = spec;
  return Object.keys(vars).length ? { ...rest, role_vars: vars } : (rest as EnclaveSpec);
};

/** The required variables no value is set for, as role.name. */
export const missingRoleVars = (spec: EnclaveSpec, roles: Role[]): string[] => {
  const used = new Set((spec.hosts || []).flatMap((h) => h.roles || []));
  return roles.filter((r) => used.has(r.name)).flatMap((r) =>
    Object.entries(r.variables || {})
      .filter(([name, v]) => v.required && spec.role_vars?.[r.name]?.[name] === undefined)
      .map(([name]) => `${r.name}.${name}`));
};

const Field: React.FC<{ role: string; name: string; meta: RoleVariable; value: unknown; disabled: boolean; onChange: (v: unknown) => void }> = ({
  role, name, meta, value, disabled, onChange,
}) => {
  const [shown, setShown] = useState(false);
  const label = `${role} ${name}`;
  if (structured(value) || structured(meta.default)) {
    return <span className="gs-muted gs-mono" aria-label={label}>{value === undefined ? 'not set' : JSON.stringify(value)} (YAML)</span>;
  }
  if (typeof meta.default === 'boolean' || typeof value === 'boolean') {
    return (
      <select aria-label={label} disabled={disabled} value={value === undefined ? '' : String(value)}
        onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value === 'true')}>
        <option value="">{meta.default === undefined ? 'not set' : `default (${String(meta.default)})`}</option>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  const numeric = typeof meta.default === 'number';
  const secret = SECRET.test(name);
  return (
    <div className="gs-row" style={{ gap: 4 }}>
      <input aria-label={label} className="gs-mono" disabled={disabled} type={secret && !shown ? 'password' : numeric ? 'number' : 'text'}
        autoComplete="off" placeholder={meta.default === undefined ? '' : String(meta.default)} value={value === undefined ? '' : String(value)}
        onChange={(e) => {
          const text = e.target.value;
          onChange(text === '' ? undefined : numeric && !Number.isNaN(Number(text)) ? Number(text) : text);
        }} />
      {secret && (
        <button className="gs-btn ghost sm" type="button" aria-label={`${shown ? 'Hide' : 'Show'} ${label}`} onClick={() => setShown(!shown)}>
          {shown ? 'Hide' : 'Show'}
        </button>
      )}
    </div>
  );
};

export const RoleVariables: React.FC<{ spec: EnclaveSpec; roles: Role[]; disabled: boolean; onChange: (s: EnclaveSpec) => void }> = ({
  spec, roles, disabled, onChange,
}) => {
  const hosts = spec.hosts || [];
  const vars = spec.role_vars || {};
  const used = roles.filter((r) => hosts.some((h) => (h.roles || []).includes(r.name)) && Object.keys(r.variables || {}).length);
  const unused = Object.keys(vars).filter((name) => !hosts.some((h) => (h.roles || []).includes(name)));
  const missing = missingRoleVars(spec, roles);

  // Opens by itself when a required value goes missing (a role was added), and otherwise as left
  const [open, setOpen] = useState(missing.length > 0);
  const before = useRef(new Set(missing));
  useEffect(() => {
    if (missing.some((m) => !before.current.has(m))) setOpen(true);
    before.current = new Set(missing);
  }, [missing.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!used.length && !unused.length) return null;
  const summary = missing.length
    ? `${missing.length} required ${missing.length === 1 ? 'value' : 'values'} not set`
    : `${used.length} ${used.length === 1 ? 'role' : 'roles'}, all set`;
  const hostsOf = (role: string) => hosts.filter((h) => (h.roles || []).includes(role)).map((h) => (h.count && h.count > 1 ? `${h.name} ×${h.count}` : h.name));

  return (
    <div style={{ marginTop: 12 }}>
      <button className="gs-row gs-plain" aria-expanded={open} onClick={() => setOpen(!open)} style={{ width: '100%', textAlign: 'left', gap: 6 }}>
        <span className="gs-muted" style={{ width: 10 }}>{open ? '▾' : '▸'}</span>
        <span>Role variables</span>
        <span className={missing.length ? '' : 'gs-muted'} style={{ fontSize: 11, color: missing.length ? 'var(--gs-bad)' : undefined }}>{summary}</span>
      </button>
      {open && (
        <table className="gs-table gs-form-table" style={{ marginTop: 4 }}>
          <tbody>
            {used.map((role) => (
              <React.Fragment key={role.name}>
                <tr>
                  <td colSpan={3}>
                    <b className="gs-mono">{role.name}</b> <span className="gs-muted">({hostsOf(role.name).join(', ')})</span>
                  </td>
                </tr>
                {Object.entries(role.variables || {}).map(([name, meta]) => (
                  <tr key={name}>
                    <td className="gs-mono" style={{ width: 160, paddingLeft: 16 }}>
                      {name}{meta.required && <span title="Required" style={{ color: 'var(--gs-bad)' }}> *</span>}
                    </td>
                    <td style={{ width: 280 }}>
                      <Field role={role.name} name={name} meta={meta} value={vars[role.name]?.[name]} disabled={disabled}
                        onChange={(v) => onChange(withRoleVar(spec, role.name, name, v))} />
                    </td>
                    <td className="gs-muted" style={{ fontSize: 11 }}>{meta.description}</td>
                  </tr>
                ))}
              </React.Fragment>
            ))}
            {unused.map((name) => (
              <tr key={name}>
                <td colSpan={2}>
                  <b className="gs-mono">{name}</b> <span className="gs-muted">unused: no host has this role ({Object.keys(vars[name] || {}).join(', ')})</span>
                </td>
                <td>
                  {!disabled && (
                    <button className="gs-x" aria-label={`Clear the variables of ${name}`} title="Clear these values"
                      onClick={() => onChange(withoutRole(spec, name))}>×</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};
