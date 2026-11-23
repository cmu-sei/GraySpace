/**
 * A host's roles: chips for the ones it has, and a menu of the catalog's roles with their whole descriptions
 * (a <datalist> cut them off, and suggested only the first of a comma-separated list).
 */
import React, { useEffect, useRef, useState } from 'react';
import { Role } from '../../types/build';

interface Props {
  value: string[];
  roles: Role[];
  /** The host's image type, to mark roles that don't run on it */
  imageType?: string;
  disabled?: boolean;
  onChange: (roles: string[]) => void;
}

export const RolePicker: React.FC<Props> = ({ value, roles, imageType, disabled, onChange }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  const available = roles.filter((r) => !value.includes(r.name));
  const unsupported = (r: Role) => Boolean(imageType && r.image_types?.length && !r.image_types.includes(imageType as 'lxc' | 'vm'));

  return (
    <div ref={ref} className="gs-role-picker" role="group" aria-label="Roles">
      {value.map((name) => (
        <span key={name} className="gs-tag" title={roles.find((r) => r.name === name)?.description}>
          {name}
          {!disabled && <button className="gs-x" aria-label={`Remove ${name}`} onClick={() => onChange(value.filter((v) => v !== name))}>×</button>}
        </span>
      ))}
      {!disabled && available.length > 0 && (
        <button className="gs-btn ghost sm" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>＋ Role</button>
      )}
      {value.length === 0 && (disabled || available.length === 0) && <span className="gs-muted">none</span>}
      {open && (
        <div className="gs-menu right gs-role-menu" role="menu">
          {available.map((r) => (
            <button key={r.name} role="menuitem" aria-label={r.name} onClick={() => { onChange([...value, r.name]); setOpen(false); }}>
              <span>
                <b className="gs-mono">{r.name}</b>
                {unsupported(r) && <span className="gs-pill warn" style={{ marginLeft: 6 }}>{r.image_types!.join(', ')} only</span>}
                <div className="gs-muted">{r.description}</div>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};
