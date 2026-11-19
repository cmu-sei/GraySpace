"""Centralized mutation tracker for persisting runtime customizations.

Tracks all user-driven changes (traffic shaping, DNS mutations,
SDN zone changes, certificates issued and revoked) to an active_mutations.json file so they
survive backend restarts. Cleared when the user resets them (POST /api/v1/changes/discard, /clear).

`preset` is the base preset last applied (services/presets.py), or None for the base as deployed.
Its changes are tracked like any other, so a reset can undo them; `changes` lists only those made
since, with who made them (X-Grayspace-User, services/request_user.py) and when, so the UI can say
what Reset changes would undo and whose it was.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from config import settings
from services import request_user

logger = logging.getLogger(__name__)


class MutationTracker:
    """Tracks and persists all runtime mutations."""

    def __init__(self):
        self.traffic_configs: Dict[str, dict] = {}  # link_id -> TrafficConfig dict
        self.dns_domains: List[dict] = []
        self.dns_records: List[dict] = []
        self.zone_changes: Dict[str, dict] = {}  # "hostname:interface" -> zone params
        self.certificates_issued: List[dict] = []  # {ca_hostname, domain, serial}
        self.certificates_revoked: List[dict] = []  # {ca_hostname, serial}: a CA can't take a revocation back
        self.changes: List[dict] = []  # {kind, key, summary, by, at}, oldest first
        self.preset: Optional[str] = None
        self._file_path: Optional[Path] = None

    def _get_file_path(self) -> Path:
        """Resolve the mutations file path (lazy, since settings may not be ready at import time)."""
        if self._file_path is None:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            self._file_path = settings.data_dir / "active_mutations.json"
        return self._file_path

    def _legacy_file_path(self) -> Path:
        """Where the file was before presets: scenarios/ next to the tfvars."""
        return Path(settings.tfvars_path).parent / "scenarios" / "active_mutations.json"

    def load(self):
        """Load persisted mutations from disk. No-op if file doesn't exist."""
        path = self._get_file_path()
        if not path.exists() and self._legacy_file_path().exists():
            path = self._legacy_file_path()
        if not path.exists():
            logger.info("No active mutations file found, starting clean")
            return

        try:
            with open(path, 'r') as f:
                data = json.load(f)
            self.traffic_configs = data.get("traffic_configs", {})
            self.dns_domains = data.get("dns_domains", [])
            self.dns_records = data.get("dns_records", [])
            self.zone_changes = data.get("zone_changes", {})
            self.certificates_issued = data.get("certificates_issued", [])
            self.certificates_revoked = data.get("certificates_revoked", [])
            # Client moves went with base clients (contract 1.18); a file from before keeps none
            self.changes = [c for c in data.get("changes", []) if c.get("kind") != "client_move"]
            # A saved scenario's name is kept as the preset; "GraySpace" was the base itself
            preset = data.get("preset", data.get("baseline_scenario"))
            self.preset = None if preset in (None, "", "GraySpace") else preset
            logger.info(f"Loaded {self.count()} active mutations from {path} (preset: {self.preset or 'none'})")
        except Exception as e:
            logger.warning(f"Failed to load mutations file: {e}")
            return
        if path != self._get_file_path():
            self.save()
            path.unlink(missing_ok=True)

    def save(self):
        """Persist current mutations to disk."""
        path = self._get_file_path()
        try:
            data = {
                "traffic_configs": self.traffic_configs,
                "dns_domains": self.dns_domains,
                "dns_records": self.dns_records,
                "zone_changes": self.zone_changes,
                "certificates_issued": self.certificates_issued,
                "certificates_revoked": self.certificates_revoked,
                "changes": self.changes,
                "preset": self.preset,
            }
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save mutations file: {e}")

    def clear(self, keep_preset: bool = False):
        """Clear all tracked mutations.

        Args:
            keep_preset: If True, clears mutation data but keeps the preset's
                name and writes an empty mutations file. If False (default),
                deletes the file entirely: no preset.
        """
        self.traffic_configs = {}
        self.dns_domains = []
        self.dns_records = []
        self.zone_changes = {}
        self.certificates_issued = []
        self.certificates_revoked = []
        self.changes = []
        if keep_preset:
            self.save()
            logger.info(f"Cleared mutations, kept preset '{self.preset}'")
        else:
            self.preset = None
            path = self._get_file_path()
            try:
                if path.exists():
                    path.unlink()
                    logger.info("Cleared active mutations file: no preset")
            except Exception as e:
                logger.warning(f"Failed to delete mutations file: {e}")

    def set_preset(self, name: Optional[str]):
        """Set the active preset (None: none) and persist. Changes are counted from it, but for
        certificates, which no preset holds."""
        self.preset = name
        self.changes = [c for c in self.changes if c["kind"].startswith("certificate_")]
        self.save()
        logger.info(f"Preset set to '{name or 'none'}'")

    def validate_against_tfvars(self, tfvars_data: dict):
        """Drop tracked mutations that reference entities no longer in tfvars."""
        changed = False

        # Validate traffic configs — both routers must exist
        routers = set(tfvars_data.get("routers", {}).keys())
        stale = []
        for link_id in self.traffic_configs:
            parts = link_id.split(":", 1)
            if len(parts) != 2 or parts[0] not in routers or parts[1] not in routers:
                stale.append(link_id)
        for link_id in stale:
            del self.traffic_configs[link_id]
            logger.info(f"Dropped stale traffic config for '{link_id}'")
            changed = True

        if changed:
            self.save()

    # --- Who changed what ---
    def _note(self, kind: str, key: str, summary: str):
        """Note a change; one per kind and key, the latest."""
        self.changes = [c for c in self.changes if (c["kind"], c["key"]) != (kind, key)]
        self.changes.append({"kind": kind, "key": key, "summary": summary, "by": request_user.current(),
                             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    def _forget(self, kind: str, key: str):
        self.changes = [c for c in self.changes if (c["kind"], c["key"]) != (kind, key)]

    def count(self) -> int:
        return (len(self.traffic_configs) + len(self.dns_domains) + len(self.dns_records)
                + len(self.zone_changes) + len(self.certificates_issued) + len(self.certificates_revoked))

    # --- Traffic shaping tracking ---
    def set_traffic_config(self, link_id: str, config: dict):
        self.traffic_configs[link_id] = config
        shown = ", ".join(f"{k} {v}" for k, v in config.items() if v not in (None, 0, False))
        self._note("traffic", link_id, f"{link_id} shaped" + (f": {shown}" if shown else ""))
        self.save()

    def remove_traffic_config(self, link_id: str):
        self.traffic_configs.pop(link_id, None)
        self._forget("traffic", link_id)
        self.save()

    # --- DNS tracking ---
    def add_dns_domain(self, params: dict):
        self.dns_domains.append(params)
        domain = params.get("domain") or params.get("name") or "?"
        self._note("dns_domain", str(domain), f"Domain {domain} created")
        self.save()

    def add_dns_record(self, params: dict):
        self.dns_records.append(params)
        name = ".".join(str(p) for p in (params.get("name"), params.get("zone")) if p and p != "@") or "?"
        self._note("dns_record", f"{name}:{params.get('type')}:{len(self.dns_records)}", f"{params.get('type', 'A')} record {name}")
        self.save()

    # --- Certificates ---
    def add_certificate_issued(self, ca_hostname: str, domain: str, serial: Optional[str]):
        self.certificates_issued.append({"ca_hostname": ca_hostname, "domain": domain, "serial": serial})
        self._note("certificate_issued", f"{ca_hostname}:{serial or domain}", f"Certificate for {domain} issued by {ca_hostname}")
        self.save()

    def add_certificate_revoked(self, ca_hostname: str, serial: str):
        self.certificates_issued = [c for c in self.certificates_issued if c.get("serial") != serial]
        self.certificates_revoked.append({"ca_hostname": ca_hostname, "serial": serial})
        self._forget("certificate_issued", f"{ca_hostname}:{serial}")
        self._note("certificate_revoked", f"{ca_hostname}:{serial}", f"Certificate {serial} revoked on {ca_hostname} (can't be undone)")
        self.save()

    # --- SDN zone tracking ---
    def set_zone_change(self, key: str, zone_id: str, vlan_tag: Optional[int] = None,
                        original_zone_id: str = "", original_vlan_tag: Optional[int] = None):
        """original_*: the interface's zone before this change. One already changed keeps the
        original of its first change; changed back to it, it's no change."""
        earlier = self.zone_changes.get(key)
        if earlier:
            original_zone_id, original_vlan_tag = earlier["original_zone_id"], earlier["original_vlan_tag"]
        if earlier and (zone_id, vlan_tag) == (original_zone_id, original_vlan_tag):
            del self.zone_changes[key]
            self._forget("zone", key)
        else:
            self.zone_changes[key] = {
                "zone_id": zone_id, "vlan_tag": vlan_tag,
                "original_zone_id": original_zone_id, "original_vlan_tag": original_vlan_tag,
            }
            self._note("zone", key, f"{key} moved to zone {zone_id}")
        self.save()


# Global instance
mutation_tracker = MutationTracker()
