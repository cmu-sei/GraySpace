"""DNS service for querying zone data from DNS containers."""
import asyncio
import re
import random
import base64
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from services.ssh_client import ssh_client
from services.topology_service import topology_service
from services.proxmox_client import proxmox_client
from services.mutation_tracker import mutation_tracker
from models.network import AddDNSRecordResult, CreateDomainResult

logger = logging.getLogger(__name__)

# Zone file paths by role
ZONE_PATHS = {
    "root-dns": ["/var/bind/db.root"],
    "tld": "/var/bind/db.{zone}",
    "hosting": None,  # Dynamic - read from config
    "resolver": [],  # No zones
}


def _parse_zone_file(content: str, zone_name: str) -> List[Dict[str, Any]]:
    """
    Parse a BIND zone file into structured records.

    Args:
        content: Raw zone file text.
        zone_name: The zone name for context.

    Returns:
        List of parsed DNS record dicts.
    """
    records = []
    default_ttl = 86400
    zone_name.rstrip(".") + "."
    in_soa = False
    soa_lines: List[str] = []
    soa_name = ""
    soa_ttl = ""
    soa_class = "IN"

    for raw_line in content.splitlines():
        # Strip comments
        line = re.sub(r";.*$", "", raw_line).strip()
        if not line:
            continue

        # Handle $TTL directive
        if line.startswith("$TTL"):
            parts = line.split()
            if len(parts) >= 2:
                default_ttl = _parse_ttl(parts[1])
            continue

        # Handle $ORIGIN directive
        if line.startswith("$ORIGIN"):
            parts = line.split()
            if len(parts) >= 2:
                parts[1]
            continue

        # Skip other directives
        if line.startswith("$"):
            continue

        # Handle multi-line SOA records
        if in_soa:
            soa_lines.append(line)
            if ")" in line:
                in_soa = False
                soa_data = " ".join(soa_lines)
                soa_data = soa_data.replace("(", "").replace(")", "").strip()
                soa_data = re.sub(r"\s+", " ", soa_data)
                records.append({
                    "name": soa_name or "@",
                    "ttl": int(soa_ttl) if soa_ttl else default_ttl,
                    "class": soa_class,
                    "type": "SOA",
                    "data": soa_data,
                })
            continue

        # Check if this starts a SOA record
        if "SOA" in line and "(" in line:
            parts = _split_record_line(line)
            if parts:
                name, ttl, rclass, rtype, rdata = parts
                in_soa = True
                soa_name = name
                soa_ttl = str(ttl) if ttl else ""
                soa_class = rclass
                soa_lines = [rdata]
                if ")" in line:
                    in_soa = False
                    soa_data = rdata.replace("(", "").replace(")", "").strip()
                    soa_data = re.sub(r"\s+", " ", soa_data)
                    records.append({
                        "name": soa_name or "@",
                        "ttl": int(soa_ttl) if soa_ttl else default_ttl,
                        "class": soa_class,
                        "type": "SOA",
                        "data": soa_data,
                    })
            continue

        # Parse normal record
        parts = _split_record_line(line)
        if parts:
            name, ttl, rclass, rtype, rdata = parts
            records.append({
                "name": name or "@",
                "ttl": ttl if ttl else default_ttl,
                "class": rclass,
                "type": rtype,
                "data": rdata,
            })

    return records


def _parse_ttl(value: str) -> int:
    """Parse a TTL value (supports numeric and BIND shorthand like 1h, 1d)."""
    value = value.strip().lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    if value[-1] in multipliers:
        try:
            return int(value[:-1]) * multipliers[value[-1]]
        except ValueError:
            pass
    try:
        return int(value)
    except ValueError:
        return 86400


def _split_record_line(line: str):
    """
    Split a DNS record line into (name, ttl, class, type, rdata).

    Returns None if the line cannot be parsed.
    """
    tokens = line.split()
    if len(tokens) < 2:
        return None

    known_types = {
        "A", "AAAA", "NS", "MX", "CNAME", "SOA", "PTR", "TXT",
        "SRV", "CAA", "DNSKEY", "DS", "RRSIG", "NSEC", "NSEC3",
    }
    known_classes = {"IN", "CH", "HS"}

    name = ""
    ttl = None
    rclass = "IN"
    rtype = None
    idx = 0

    # First token: name or TTL or class or type
    if tokens[0] not in known_classes and tokens[0].upper() not in known_types:
        try:
            ttl = int(tokens[0])
        except ValueError:
            name = tokens[0]
            idx = 1

    # Look for TTL, class, type in remaining tokens
    while idx < len(tokens) and rtype is None:
        token = tokens[idx]
        token_upper = token.upper()
        if token_upper in known_types:
            rtype = token_upper
            idx += 1
            break
        elif token_upper in known_classes:
            rclass = token_upper
            idx += 1
        else:
            try:
                ttl = int(token)
                idx += 1
            except ValueError:
                # Must be part of type or something unexpected
                idx += 1
                break

    if rtype is None:
        return None

    rdata = " ".join(tokens[idx:])
    return name, ttl, rclass, rtype, rdata


class DNSService:
    """Service for querying DNS zone data from containers."""

    async def get_zones(self, hostname: str) -> Dict[str, Any]:
        """
        Get DNS zone data for a specific DNS server by SSHing in and reading zone files.

        Args:
            hostname: DNS server hostname.

        Returns:
            Dict with hostname, role, and zones list.
        """
        # Find DNS server info from tfvars
        dns_data = topology_service.tfvars_data.get("core_dns", {}).get(hostname)
        if not dns_data:
            return {"hostname": hostname, "role": "unknown", "zones": []}

        role = dns_data.get("role", "")
        zone_names = dns_data.get("zones", [])

        try:
            zones = await self._fetch_zones(hostname, role, zone_names)
        except Exception as e:
            logger.warning(f"Failed to fetch zones from {hostname}: {e}")
            zones = []

        return {
            "hostname": hostname,
            "role": role,
            "zones": zones,
        }

    async def _fetch_zones(
        self, hostname: str, role: str, zone_names: List[str]
    ) -> List[Dict[str, Any]]:
        """Fetch and parse zone files based on the server's role."""
        zones = []

        # tfvars stores zones as comma-separated strings (e.g. ['com,net,org'])
        expanded_zones = []
        for entry in zone_names:
            expanded_zones.extend(entry.split(","))

        if role == "root-dns":
            content = await self._read_file(hostname, "/var/bind/db.root")
            if content:
                records = _parse_zone_file(content, ".")
                zones.append({"name": ".", "records": records})

        elif role == "tld":
            for zone_name in expanded_zones:
                path = f"/var/bind/db.{zone_name}"
                content = await self._read_file(hostname, path)
                if content:
                    records = _parse_zone_file(content, zone_name)
                    zones.append({"name": zone_name, "records": records})

        elif role == "hosting":
            # Try reading BIND config to find zone file paths
            config_content = await self._read_file(
                hostname, "/etc/bind/named.conf"
            )
            if config_content:
                # Follow includes
                all_config = config_content
                includes = re.findall(r'include\s+"([^"]+)"', config_content)
                for inc_path in includes:
                    inc_content = await self._read_file(hostname, inc_path)
                    if inc_content:
                        all_config += "\n" + inc_content

                zone_files = self._parse_zone_config(all_config)
                for zone_name, zone_path in zone_files:
                    content = await self._read_file(hostname, zone_path)
                    if content:
                        records = _parse_zone_file(content, zone_name)
                        zones.append({"name": zone_name, "records": records})
            else:
                # Fallback: try zone names from tfvars
                for zone_name in expanded_zones:
                    path = f"/var/bind/db.{zone_name}"
                    content = await self._read_file(hostname, path)
                    if content:
                        records = _parse_zone_file(content, zone_name)
                        zones.append({"name": zone_name, "records": records})

        # resolver role has no zones to read

        return zones

    def _resolve_host(self, hostname: str) -> Optional[str]:
        """Resolve a container hostname to its management IP via Proxmox."""
        ip = proxmox_client.get_container_management_ip(hostname)
        if not ip:
            logger.warning(f"Could not resolve management IP for {hostname}")
        return ip

    async def _read_file(self, hostname: str, path: str) -> Optional[str]:
        """Read a file from the remote host via SSH (resolves hostname to management IP)."""
        ip = await asyncio.to_thread(self._resolve_host, hostname)
        if not ip:
            return None
        try:
            exit_code, stdout, stderr = await ssh_client.execute_command(
                ip, f"cat {path}", timeout=10
            )
            if exit_code == 0:
                return stdout
            logger.warning(f"Failed to read {path} from {hostname} ({ip}): exit_code={exit_code}, stderr={stderr.strip()}")
            return None
        except Exception as e:
            logger.warning(f"SSH error reading {path} from {hostname} ({ip}): {e}")
            return None

    def _parse_zone_config(self, config: str) -> List[tuple]:
        """
        Parse BIND zone config to extract zone name and file path pairs.

        Example config line:
            zone "example.com" { type master; file "/var/bind/db.example.com"; };
        """
        results = []
        zone_pattern = re.compile(
            r'zone\s+"([^"]+)"\s*\{[^}]*file\s+"([^"]+)"', re.DOTALL
        )
        for match in zone_pattern.finditer(config):
            zone_name = match.group(1)
            zone_file = match.group(2)
            results.append((zone_name, zone_file))
        return results

    async def _write_file(self, hostname: str, path: str, content: str) -> bool:
        """Write file to remote host via SSH using base64 encoding."""
        ip = await asyncio.to_thread(self._resolve_host, hostname)
        if not ip:
            return False
        b64 = base64.b64encode(content.encode()).decode()
        cmd = f"echo '{b64}' | base64 -d > {path}"
        try:
            exit_code, _, stderr = await ssh_client.execute_command(ip, cmd, timeout=10)
            if exit_code != 0:
                logger.warning(f"Failed to write {path} on {hostname}: {stderr.strip()}")
                return False
            return True
        except Exception as e:
            logger.warning(f"SSH error writing {path} on {hostname}: {e}")
            return False

    async def _run_command(self, hostname: str, cmd: str) -> Tuple[int, str, str]:
        """Execute a command on remote host via SSH."""
        ip = await asyncio.to_thread(self._resolve_host, hostname)
        if not ip:
            return (1, "", "Could not resolve management IP")
        try:
            return await ssh_client.execute_command(ip, cmd, timeout=15)
        except Exception as e:
            return (1, "", str(e))

    async def _reload_zone(self, hostname: str, zone_name: Optional[str] = None) -> bool:
        """Reload BIND zone via rndc."""
        cmd = f"rndc reload {zone_name}" if zone_name else "rndc reload"
        exit_code, _, stderr = await self._run_command(hostname, cmd)
        if exit_code != 0:
            logger.warning(f"rndc reload failed on {hostname}: {stderr.strip()}")
            return False
        return True

    def _increment_serial(self, content: str) -> str:
        """Increment the SOA serial number in zone file content."""
        serial_pattern = re.compile(r'(\d{8,10})(\s*;\s*serial)', re.IGNORECASE)
        match = serial_pattern.search(content)
        if not match:
            # Fallback: try to find any number that looks like a serial in SOA
            soa_pattern = re.compile(r'SOA\s+\S+\s+\S+\s*\(\s*(\d+)', re.IGNORECASE)
            match2 = soa_pattern.search(content)
            if match2:
                old_serial = int(match2.group(1))
                new_serial = old_serial + 1
                return content[:match2.start(1)] + str(new_serial) + content[match2.end(1):]
            return content

        old_serial = match.group(1)
        today = datetime.now().strftime("%Y%m%d")
        if len(old_serial) == 10 and old_serial[:8] == today:
            # Same day: increment NN
            nn = int(old_serial[8:]) + 1
            new_serial = f"{today}{nn:02d}"
        elif len(old_serial) >= 8:
            new_serial = f"{today}01"
            # If new serial would be <= old, just increment
            if int(new_serial) <= int(old_serial):
                new_serial = str(int(old_serial) + 1)
        else:
            new_serial = str(int(old_serial) + 1)

        return content[:match.start(1)] + new_serial + content[match.end(1):]

    def _find_tld_server(self, tld: str) -> Optional[Dict[str, Any]]:
        """Find the TLD server that handles the given TLD."""
        core_dns = topology_service.tfvars_data.get("core_dns", {})
        for hostname, data in core_dns.items():
            if data.get("role") != "tld":
                continue
            zones = data.get("zones", [])
            expanded = []
            for entry in zones:
                expanded.extend(entry.split(","))
            if tld in expanded:
                return {"hostname": hostname, **data}
        return None

    def _get_hosting_servers(self) -> List[Dict[str, Any]]:
        """Return list of deployed hosting DNS servers."""
        core_dns = topology_service.tfvars_data.get("core_dns", {})
        servers = []
        for hostname, data in core_dns.items():
            if data.get("role") == "hosting":
                servers.append({"hostname": hostname, **data})
        return servers

    async def _get_zone_file_path(self, hostname: str, role: str, zone_name: str) -> Optional[str]:
        """Resolve the file path for a given zone on a server."""
        if role == "root-dns":
            return "/var/bind/db.root"
        elif role == "tld":
            return f"/var/bind/db.{zone_name}"
        elif role == "hosting":
            # Parse named.conf + dynamic.conf to find the zone file
            config_content = await self._read_file(hostname, "/etc/bind/named.conf")
            all_config = config_content or ""
            if config_content:
                includes = re.findall(r'include\s+"([^"]+)"', config_content)
                for inc_path in includes:
                    inc_content = await self._read_file(hostname, inc_path)
                    if inc_content:
                        all_config += "\n" + inc_content
            zone_files = self._parse_zone_config(all_config)
            for zn, zpath in zone_files:
                if zn == zone_name:
                    return zpath
            # Default path for new zones
            return f"/var/bind/db.{zone_name}"
        return None

    @staticmethod
    def _delegation_path(tld: str) -> str:
        return f"/var/bind/delegations.{tld}"

    @staticmethod
    def _is_delegation_line(line: str, domain_fqdn: str) -> bool:
        """Whether a zone line is the NS or glue record of a delegated domain."""
        tokens = line.split()
        return bool(tokens) and tokens[0] in (domain_fqdn, f"ns1.{domain_fqdn}")

    async def _add_delegation(self, tld_hostname: str, tld: str, domain_fqdn: str, hosting_ip: str) -> Tuple[bool, str]:
        """Delegate a domain from its TLD server. Returns (ok, message): a warning when ok.

        The records go in /var/bind/delegations.<tld>, which the TLD zone pulls in with $INCLUDE.
        task configure re-templates the zone file but never overwrites that file, so runtime
        domains survive it. Zone files templated before the
        include existed get the $INCLUDE line appended. Re-delegating a domain replaces its lines.
        """
        zone_path = f"/var/bind/db.{tld}"
        path = self._delegation_path(tld)
        include = f"$INCLUDE {path}"
        code, _, stderr = await self._run_command(
            tld_hostname,
            f"touch {path} && chown named:named {path} && "
            f"(grep -qxF '{include}' {zone_path} || printf '\\n%s\\n' '{include}' >> {zone_path})",
        )
        if code != 0:
            return False, f"Could not prepare {path} on {tld_hostname}: {stderr.strip()}"

        content = await self._read_file(tld_hostname, path) or ""
        lines = [line for line in content.splitlines() if not self._is_delegation_line(line, domain_fqdn)]
        lines += [f"{domain_fqdn}\t\tIN\tNS\tns1.{domain_fqdn}", f"ns1.{domain_fqdn}\tIN\tA\t{hosting_ip}"]
        if not await self._write_file(tld_hostname, path, "\n".join(lines) + "\n"):
            return False, f"Failed to write {path} on {tld_hostname}"
        # BIND checks the zone file's mtime on reload; touch it so the include change is picked up.
        await self._run_command(tld_hostname, f"chown named:named {path} && touch {zone_path}")
        if not await self._reload_zone(tld_hostname, tld):
            return True, f"TLD delegation written but rndc reload failed on {tld_hostname}"
        return True, ""

    async def _remove_delegation(self, tld_hostname: str, tld: str, domain_fqdn: str) -> bool:
        """Remove a domain's delegation from the TLD's delegation file, and from the zone file
        itself, where older deployments wrote it. False if neither had it."""
        zone_path = f"/var/bind/db.{tld}"
        found = False
        for path in (self._delegation_path(tld), zone_path):
            content = await self._read_file(tld_hostname, path)
            if not content:
                continue
            lines = content.splitlines(keepends=True)
            kept = [line for line in lines if not self._is_delegation_line(line, domain_fqdn)]
            if len(kept) == len(lines):
                continue
            found = True
            new_content = "".join(kept)
            if path == zone_path:
                new_content = self._increment_serial(new_content)
            await self._write_file(tld_hostname, path, new_content)
            await self._run_command(tld_hostname, f"chown named:named {path}")
        if found:
            await self._run_command(tld_hostname, f"touch {zone_path}")
            await self._reload_zone(tld_hostname, tld)
        return found

    async def add_record(
        self, hostname: str, zone: str, name: str, rtype: str, data: str, ttl: int = 3600, track: bool = True
    ) -> AddDNSRecordResult:
        """Add a DNS record to a zone on a server.

        track records the change in the overlay (active_mutations.json). Slices pass False and
        keep their records in the instance, so quick reset leaves them alone.
        """
        warnings = []
        # Get server info
        dns_data = topology_service.tfvars_data.get("core_dns", {}).get(hostname)
        if not dns_data:
            return AddDNSRecordResult(success=False, message=f"DNS server '{hostname}' not found")

        role = dns_data.get("role", "")
        zone_path = await self._get_zone_file_path(hostname, role, zone)
        if not zone_path:
            return AddDNSRecordResult(success=False, message=f"Could not determine zone file path for zone '{zone}'")

        # Read current zone file
        content = await self._read_file(hostname, zone_path)
        if not content:
            return AddDNSRecordResult(success=False, message=f"Could not read zone file at {zone_path}")

        # Increment SOA serial
        content = self._increment_serial(content)

        # Append record
        record_line = f"{name}\t{ttl}\tIN\t{rtype}\t{data}\n"
        if not content.endswith("\n"):
            content += "\n"
        content += record_line

        # Write back
        if not await self._write_file(hostname, zone_path, content):
            return AddDNSRecordResult(success=False, message=f"Failed to write zone file at {zone_path}")

        # Fix ownership
        await self._run_command(hostname, f"chown named:named {zone_path}")

        # Reload zone
        reload_zone = zone if zone != "." else None
        if not await self._reload_zone(hostname, reload_zone):
            warnings.append("Zone file written but rndc reload failed. BIND may need manual restart.")

        if track:
            mutation_tracker.add_dns_record({
                "server": hostname, "zone": zone, "name": name,
                "type": rtype, "data": data, "ttl": ttl,
            })

        return AddDNSRecordResult(
            success=True,
            message=f"Added {rtype} record for '{name}' to zone '{zone}' on {hostname}",
            warnings=warnings,
        )

    async def create_domain(
        self,
        domain: str,
        hosting_server: Optional[str] = None,
        ip_address: Optional[str] = None,
        mx_records: Optional[str] = None,
        additional_records: Optional[List[dict]] = None,
        create_www: bool = True,
        track: bool = True,
    ) -> CreateDomainResult:
        """Create a full domain with TLD delegation and hosting zone file.

        track records the domain in the overlay (active_mutations.json); slices pass False.
        """
        warnings = []
        records_created = 0

        # Parse TLD from domain
        parts = domain.rstrip(".").split(".")
        if len(parts) < 2:
            return CreateDomainResult(success=False, message=f"Invalid domain format: '{domain}'. Expected format: example.com")
        tld = parts[-1]

        # Find TLD server
        tld_data = self._find_tld_server(tld)
        if not tld_data:
            return CreateDomainResult(success=False, message=f"No TLD server found for '.{tld}'. Cannot create delegation.")
        tld_hostname = tld_data["hostname"]

        # Pick hosting server
        if hosting_server:
            hosting_data = topology_service.tfvars_data.get("core_dns", {}).get(hosting_server)
            if not hosting_data or hosting_data.get("role") != "hosting":
                return CreateDomainResult(success=False, message=f"'{hosting_server}' is not a valid hosting DNS server")
            hosting_hostname = hosting_server
        else:
            hosting_servers = self._get_hosting_servers()
            if not hosting_servers:
                return CreateDomainResult(success=False, message="No hosting DNS servers available")
            chosen = random.choice(hosting_servers)
            hosting_hostname = chosen["hostname"]
            hosting_data = topology_service.tfvars_data.get("core_dns", {}).get(hosting_hostname)

        hosting_ip = hosting_data.get("ip_address", "").split("/")[0]
        if not hosting_ip:
            return CreateDomainResult(success=False, message=f"Hosting server '{hosting_hostname}' has no IP address configured")

        domain_fqdn = domain.rstrip(".") + "."

        # === TLD SERVER: Add delegation ===
        ok, message = await self._add_delegation(tld_hostname, tld, domain_fqdn, hosting_ip)
        if not ok:
            return CreateDomainResult(success=False, message=message)
        if message:
            warnings.append(message)
        records_created += 2

        # === HOSTING SERVER: Create zone file ===
        today = datetime.now().strftime("%Y%m%d")
        serial = f"{today}01"

        zone_content = f"""$TTL 3600
@\tIN\tSOA\tns1.{domain_fqdn} admin.{domain_fqdn} (
\t\t{serial}\t; serial
\t\t3600\t\t; refresh
\t\t900\t\t; retry
\t\t604800\t\t; expire
\t\t86400\t\t; minimum TTL
)
@\t\t\tIN\tNS\tns1.{domain_fqdn}
ns1\t\t\tIN\tA\t{hosting_ip}
"""
        records_created += 2  # SOA is implicit, count NS + glue A

        if ip_address:
            zone_content += f"@\t\t\tIN\tA\t{ip_address}\n"
            records_created += 1
            if create_www:
                zone_content += f"www\t\t\tIN\tCNAME\t{domain_fqdn}\n"
                records_created += 1

        if mx_records:
            zone_content += f"@\t\t\tIN\tMX\t{mx_records}\n"
            records_created += 1

        if additional_records:
            for rec in additional_records:
                rname = rec.get("name", "@")
                rtype = rec.get("type", "A")
                rdata = rec.get("data", "")
                rttl = rec.get("ttl", 3600)
                if rdata:
                    zone_content += f"{rname}\t{rttl}\tIN\t{rtype}\t{rdata}\n"
                    records_created += 1

        zone_file_path = f"/var/bind/db.{domain.rstrip('.')}"
        if not await self._write_file(hosting_hostname, zone_file_path, zone_content):
            return CreateDomainResult(
                success=False,
                message=f"TLD delegation created but failed to write zone file on {hosting_hostname}",
                tld_server=tld_hostname,
                warnings=warnings,
            )
        await self._run_command(hosting_hostname, f"chown named:named {zone_file_path}")

        # Append zone declaration to dynamic.conf
        dynamic_conf_path = "/etc/bind/zones.d/dynamic.conf"
        dynamic_content = await self._read_file(hosting_hostname, dynamic_conf_path) or ""

        # Check if zone already declared
        domain_clean = domain.rstrip(".")
        if f'zone "{domain_clean}"' not in dynamic_content:
            if not dynamic_content.endswith("\n") and dynamic_content:
                dynamic_content += "\n"
            dynamic_content += f'zone "{domain_clean}" {{ type master; file "{zone_file_path}"; }};\n'
            if not await self._write_file(hosting_hostname, dynamic_conf_path, dynamic_content):
                warnings.append("Zone file created but failed to update dynamic.conf")
            await self._run_command(hosting_hostname, f"chown named:named {dynamic_conf_path}")

        if not await self._reload_zone(hosting_hostname):
            warnings.append(f"Zone file created but rndc reload failed on {hosting_hostname}")

        if track:
            mutation_tracker.add_dns_domain({
                "domain": domain, "hosting_server": hosting_hostname,
                "tld_server": tld_hostname, "ip_address": ip_address,
                "mx_records": mx_records, "create_www": create_www,
                "additional_records": additional_records or [],
            })

        return CreateDomainResult(
            success=True,
            message=f"Domain '{domain}' created successfully",
            hosting_server=hosting_hostname,
            tld_server=tld_hostname,
            records_created=records_created,
            warnings=warnings,
        )

    async def revert_record(self, record: dict) -> Tuple[bool, str]:
        """Remove a previously added DNS record from a zone file."""
        hostname = record.get("server", "")
        zone = record.get("zone", "")
        name = record.get("name", "")
        rtype = record.get("type", "")
        data = record.get("data", "")
        ttl = record.get("ttl", 3600)

        dns_data = topology_service.tfvars_data.get("core_dns", {}).get(hostname)
        if not dns_data:
            return False, f"DNS server '{hostname}' not found"

        role = dns_data.get("role", "")
        zone_path = await self._get_zone_file_path(hostname, role, zone)
        if not zone_path:
            return False, f"Could not find zone file for '{zone}' on {hostname}"

        content = await self._read_file(hostname, zone_path)
        if not content:
            return False, f"Could not read zone file at {zone_path}"

        # Remove the matching record line: the whole line, so "a" doesn't also remove "ba"
        record_line = f"{name}\t{ttl}\tIN\t{rtype}\t{data}"
        lines = content.splitlines(keepends=True)
        new_lines = [line for line in lines if line.rstrip("\r\n") != record_line]
        if len(new_lines) == len(lines):
            return False, "Record line not found in zone file"

        content = "".join(new_lines)
        content = self._increment_serial(content)

        if not await self._write_file(hostname, zone_path, content):
            return False, "Failed to write zone file"
        await self._run_command(hostname, f"chown named:named {zone_path}")
        reload_zone = zone if zone != "." else None
        await self._reload_zone(hostname, reload_zone)
        return True, f"Removed {rtype} record '{name}' from {zone} on {hostname}"

    async def revert_domain(self, domain_info: dict) -> Tuple[bool, str]:
        """Remove a previously created domain: delete zone file, dynamic.conf entry, and TLD delegation."""
        domain = domain_info.get("domain", "")
        hosting_hostname = domain_info.get("hosting_server", "")
        tld_hostname = domain_info.get("tld_server", "")
        errors = []

        domain_fqdn = domain.rstrip(".") + "."

        # 1. Remove zone file and dynamic.conf entry on hosting server
        if hosting_hostname:
            zone_file_path = f"/var/bind/db.{domain}"
            await self._run_command(hosting_hostname, f"rm -f {zone_file_path}")

            dynamic_conf_path = "/etc/bind/zones.d/dynamic.conf"
            content = await self._read_file(hosting_hostname, dynamic_conf_path)
            if content:
                # Only this zone's declaration: "bank.com" must not remove "meridianbank.com"
                declaration = f'zone "{domain.rstrip(".")}"'
                lines = content.splitlines(keepends=True)
                new_lines = [line for line in lines if not line.strip().startswith(declaration)]
                await self._write_file(hosting_hostname, dynamic_conf_path, "".join(new_lines))
                await self._run_command(hosting_hostname, f"chown named:named {dynamic_conf_path}")

            await self._reload_zone(hosting_hostname)

        # 2. Remove the NS delegation from the TLD
        if tld_hostname:
            tld = domain.rstrip(".").rsplit(".", 1)[-1]
            if not await self._remove_delegation(tld_hostname, tld, domain_fqdn):
                errors.append(f"No delegation found for {domain} in TLD zone")

        if errors:
            return False, "; ".join(errors)
        return True, f"Domain '{domain}' removed from {hosting_hostname} and {tld_hostname}"


# Global DNS service instance
dns_service = DNSService()
