"""The stages of create, destroy and plan jobs.

Create: validate → allocate → render → apply → wait → configure → dns → conditions → check → ready.
Each stage is idempotent and recorded in the instance when done, so a failed job's retry resumes at
the stage that failed. Destroy works from any state, including a failed create.
"""
import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from config import settings
from models.instances import (
    BaseAttachment,
    CreatedDomain,
    CreatedRecord,
    InstanceRecord,
    IssuedCertificate,
    JobRecord,
    STAGE_TITLES,
    LinkConditionApplied,
)
from models.slices import SliceSpec
from services import events
from services.base_service import version_ref
from services.slices import catalog, environment, manifest, plan, render, service, store
from services.slices.runner import FAILURE_HOOKS, KINDS, JobContext, StageError

CREATE_STAGES = ["validate", "allocate", "render", "apply", "wait", "configure", "dns", "conditions", "check", "ready"]
DESTROY_STAGES = ["revoke", "dns", "conditions", "detach", "destroy", "workspace", "images", "free"]
PLAN_STAGES = ["validate", "allocate", "render", "plan"]

TITLES = {
    "validate": "Check the enclave against the base",
    "allocate": "Assign addresses and names",
    "render": "Issue certificates and prepare the build",
    "apply": "Create the hosts",
    "wait": "Wait for the hosts to start",
    "configure": "Configure the hosts",
    "dns": "Create DNS records",
    "conditions": "Apply link conditions",
    "check": "Check reachability",
    "ready": "Finish",
    "revoke": "Revoke certificates",
    "detach": "Disconnect from the base routers",
    "destroy": "Remove the hosts",
    "workspace": "Clean up",
    "images": "Remove unused images",
    "free": "Release the addresses",
    "plan": "Preview the changes",
}


STAGE_TITLES["create_instance"] = {**{s: TITLES[s] for s in CREATE_STAGES}, "player": "Add its hosts to the Player view"}
STAGE_TITLES["destroy_instance"] = {**{s: TITLES[s] for s in DESTROY_STAGES}, "dns": "Remove DNS records",
                                    "conditions": "Undo link conditions", "player": "Remove its hosts from the Player view"}


def with_player(stages: List[str], first: bool = False) -> List[str]:
    """An instance job outside a rollout updates the Player view when there is one: after a create,
    and before a destroy, so the view never shows VMs that are gone (a rollout's children don't:
    the rollout's player stage does, once)."""
    from services import player

    if not (player.enabled() and player.applied() is not None):
        return stages
    return ["player"] + stages if first else stages + ["player"]


async def stage_player(ctx: JobContext, exclude: Optional[str] = None):
    from services import player, rollout

    draft = rollout.get_draft()
    if draft.player is None:
        ctx.log("No Player view in the draft")
        return
    await player.apply(ctx, draft, exclude={exclude} if exclude else frozenset())
STAGE_TITLES["plan_slice"] = {**{s: TITLES[s] for s in PLAN_STAGES}, "render": "Prepare the build"}


# --- Paths and helpers ---


def instance_tfvars(instance_id: str):
    return environment.slice_module() / "instances" / f"{instance_id}.tfvars.json"


def images_tfvars():
    return environment.images_module() / "images.tfvars.json"


def inventory_file(instance_id: str):
    return environment.inventory_dir() / f"{instance_id}.ini"


def vars_file(instance_id: str):
    return environment.inventory_dir() / f"{instance_id}.vars.yml"


def crucible_team_id() -> Optional[str]:
    return environment.crucible_team_id()


def registered_in_crucible(instance_id: str) -> bool:
    try:
        tfvars = json.loads(instance_tfvars(instance_id).read_text())
        return bool(tfvars.get("crucible_team_id") and tfvars.get("crucible_guests"))
    except (OSError, ValueError):
        return False


def get_record(instance_id: str) -> InstanceRecord:
    record = store.get_instance(instance_id)
    if record is None:
        raise StageError(f"No instance '{instance_id}'")
    return record


def set_state(instance_id: str, state: str, error: Optional[str] = None) -> InstanceRecord:
    with store.update_instance(instance_id) as record:
        record.state = state
        record.error = error
    events.publish_instance(record)
    return record


def spec_of(record: InstanceRecord) -> SliceSpec:
    stored = store.get_slice(record.slice.slug, record.slice.version)
    if stored is None:
        raise StageError(f"Slice {record.slice.slug} version {record.slice.version} is no longer stored")
    return stored[1]


def render_env() -> render.RenderEnv:
    env = environment.job_env()
    try:
        r = render.RenderEnv.from_env(env)
    except ValueError as e:
        raise StageError(str(e))
    if len(r.environment_id) > 4:
        raise StageError(f"TF_VAR_environment_id '{r.environment_id}' is longer than 4 characters; slice vnet IDs would exceed Proxmox's 8")
    return r


def tf_env(workspace: Optional[str] = None) -> Dict[str, str]:
    """Jobs never change the module's selected workspace: each command names its own through
    TF_WORKSPACE. Workspace management commands (new, select, delete) run without it."""
    env = environment.job_env()
    if workspace:
        env["TF_WORKSPACE"] = workspace
    else:
        env.pop("TF_WORKSPACE", None)
    return env


# What terraform init says when a provider's version constraint moved past the work tree's lock file
LOCK_OUTDATED = "terraform init -upgrade"


async def tf_init(ctx: JobContext, module, quiet: bool = True):
    """terraform init, and init -upgrade only when Terraform says the lock file no longer satisfies
    a provider's constraint (a constraint changed in the code; each work tree keeps its own
    .terraform.lock.hcl). Otherwise the locked versions stay, as task init's sources check keeps them."""
    rc, out = await ctx.run(["terraform", "init", "-input=false"], module, env=tf_env(), check=False, quiet=quiet)
    if rc == 0:
        return
    if LOCK_OUTDATED not in out:
        raise StageError(f"terraform init exited {rc}: {out[-1500:]}")
    ctx.log("A provider's version constraint changed since the lock file was written: terraform init -upgrade")
    await ctx.run(["terraform", "init", "-input=false", "-upgrade"], module, env=tf_env(), quiet=quiet)


async def tf_workspaces(ctx: JobContext, module) -> List[str]:
    _, out = await ctx.run(["terraform", "workspace", "list"], module, env=tf_env(), quiet=True)
    return [line.strip(" *") for line in out.splitlines() if line.strip(" *")]


async def ensure_workspace(ctx: JobContext, module, name: str):
    """Create a workspace. `workspace new` also selects it, so select default again: the
    workspace can then be deleted later, and nothing else runs in it by accident."""
    if name not in await tf_workspaces(ctx, module):
        await ctx.run(["terraform", "workspace", "new", name], module, env=tf_env(), quiet=True)
    await ctx.run(["terraform", "workspace", "select", "default"], module, env=tf_env(), quiet=True)


async def delete_workspace(ctx: JobContext, module, name: str, check: bool = True):
    """Delete a workspace, selecting default first (Terraform won't delete the selected one)."""
    await ctx.run(["terraform", "workspace", "select", "default"], module, env=tf_env(), quiet=True, check=check)
    await ctx.run(["terraform", "workspace", "delete", name], module, env=tf_env(), check=check, quiet=not check)


GUEST_TYPES = ("proxmox_virtual_environment_container", "proxmox_virtual_environment_vm")


def state_guests(module, workspace: Optional[str] = None) -> Dict[int, str]:
    """The guests a module's local state records: VM ID -> resource address. Empty without a state."""
    module = Path(module)
    path = module / "terraform.tfstate" if workspace in (None, "default") else module / "terraform.tfstate.d" / workspace / "terraform.tfstate"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    guests = {}
    for resource in state.get("resources") or []:
        if resource.get("mode") != "managed" or resource.get("type") not in GUEST_TYPES:
            continue
        prefix = f"{resource['module']}." if resource.get("module") else ""
        for instance in resource.get("instances") or []:
            vm_id = (instance.get("attributes") or {}).get("vm_id")
            if not isinstance(vm_id, int):
                continue
            key = f"[{json.dumps(instance['index_key'])}]" if "index_key" in instance else ""
            guests[vm_id] = f"{prefix}{resource['type']}.{resource['name']}{key}"
    return guests


async def check_stuck_creates(ctx: JobContext, module, workspace: Optional[str] = None):
    """Fail when a guest the state records still holds Proxmox's create lock.

    A create that dies part way (a storage cfs-lock timeout when many creates run at once) can leave
    CT<vmid>, stopped, untagged and locked, recorded in the state as tainted. The lock never clears,
    so every apply's replace fails with "CT is locked (create)" (the provider retries the delete,
    then gives up) and converge spends its attempts on it. No Terraform of ours runs while this does
    (the job holds the node lock), so a create lock on our VM ID is stale. When Proxmox can't be
    asked, the check is skipped."""
    guests = state_guests(module, workspace)
    if not guests:
        return
    from services.proxmox_client import proxmox_client

    locked = await asyncio.to_thread(proxmox_client.get_locked_guests)
    if locked is None:
        ctx.log("Couldn't list Proxmox's locked guests; not checking for failed creates")
        return
    stuck = [g for g in locked if g["lock"] == "create" and g["vmid"] in guests]
    if not stuck:
        return
    tool = {"lxc": "pct", "qemu": "qm"}
    commands = "; ".join(f"{tool[g['resource_type']]} unlock {g['vmid']} && {tool[g['resource_type']]} destroy {g['vmid']} --purge"
                         for g in stuck)
    raise StageError(
        "A create failed part way and left Proxmox's create lock on "
        + ", ".join(f"{guests[g['vmid']]} ({'CT' if g['resource_type'] == 'lxc' else 'VM'} {g['vmid']} on {g['node']})" for g in stuck)
        + ". Terraform can't replace it while the lock is held. On the Proxmox host, as root: "
        + commands + "; then retry the job (the next apply creates them again)")


async def converge(ctx: JobContext, module, args: List[str], workspace: Optional[str] = None, what: str = "apply"):
    """Apply until `terraform plan -detailed-exitcode` exits 0 (not grepping for "No changes.").

    Each attempt plans to a file and applies that file, so the state is refreshed once per attempt
    (by the plan), not by the apply and again by the check; a saved plan carries its variables, so
    the apply gets no args. The file holds sensitive values in clear: mkstemp makes it 0600, and it
    is removed when converge returns. The plan, which only reads, runs at the plan parallelism; the
    apply at the apply parallelism, which bounds concurrent creates on the cluster's storage
    (environment.parallelism). Before each plan it fails fast on a guest stuck in a failed create
    (check_stuck_creates), which Terraform can't replace while Proxmox holds its create lock."""
    attempts = settings.slice_apply_attempts
    env = tf_env(workspace)
    fd, planfile = tempfile.mkstemp(prefix="grayspace-converge-", suffix=".tfplan")
    os.close(fd)
    try:
        # One plan per attempt and one after the last apply
        for i in range(1, attempts + 2):
            ctx.check_cancel()
            await check_stuck_creates(ctx, module, workspace)
            if i <= attempts:
                ctx.attempt(f"{what} attempt {i}/{attempts}")
            rc, _ = await ctx.run(["terraform", "plan", "-detailed-exitcode", "-input=false", *args,
                                   f"-parallelism={environment.parallelism('plan')}", f"-out={planfile}"],
                                  module, env=env, check=False)
            if rc == 0:
                ctx.log(f"Converged after {i - 1} applies")
                ctx.attempt(None)
                return
            if rc != 2:
                ctx.log(f"Plan failed (exit {rc})")
            if i > attempts or rc != 2:
                continue
            ctx.check_cancel()
            await ctx.run(["terraform", "apply", "-auto-approve", "-input=false", f"-parallelism={environment.parallelism('apply')}", planfile],
                          module, env=env, check=False)
    finally:
        os.unlink(planfile)
    raise StageError(f"Terraform did not converge after {attempts} attempts; see the log, then retry the job")


async def tf_outputs(ctx: JobContext, workspace: str) -> dict:
    _, out = await ctx.run(["terraform", "output", "-json"], environment.slice_module(), env=tf_env(workspace), quiet=True)
    try:
        return {k: v.get("value") for k, v in json.loads(out or "{}").items()}
    except json.JSONDecodeError as e:
        raise StageError(f"terraform output -json returned invalid JSON: {e}")


async def ssh(ip: str, command: str, timeout: int = 30) -> tuple:
    from services.ssh_client import ssh_client

    try:
        code, out, err = await ssh_client.execute_command(ip, command, timeout=timeout)
        return code, (out + err).strip()
    except Exception as e:
        return 255, str(e)


async def base_mgmt_ip(hostname: str) -> Optional[str]:
    from services.proxmox_client import proxmox_client

    return await asyncio.to_thread(proxmox_client.get_container_management_ip, hostname)


def images_in_use(exclude: Optional[str] = None, extra=None) -> dict:
    names = set(extra or [])
    for inst in store.live_instances():
        if inst.id != exclude and inst.allocation:
            names |= set(inst.allocation.images)
    available = catalog.images()
    return {n: available[n] for n in sorted(names) if n in available}


def write_images_tfvars(env: render.RenderEnv, images: dict):
    store.write_text(images_tfvars(), render.dumps(images_tfvars_content(env, images)))


def images_tfvars_content(env, images):
    return render.images_tfvars(env, images.values())


async def apply_images(ctx: JobContext, env: render.RenderEnv, exclude: Optional[str] = None, extra=None):
    write_images_tfvars(env, images_in_use(exclude=exclude, extra=extra))
    module = environment.images_module()
    await tf_init(ctx, module)
    await converge(ctx, module, [f"-var-file={images_tfvars()}"], what="images apply")


def ansible_env() -> Dict[str, str]:
    env = environment.job_env()
    # The base dynamic inventory reads live instances' attachments from here.
    env["GRAYSPACE_DATA_PATH"] = str(store.data_dir())
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    return env


async def attach_routers(ctx: JobContext, routers: List[str]):
    """Re-template FRR on the base routers a routed site attaches to, and only those."""
    if not routers:
        return
    await ctx.run(["ansible-playbook", "slice-attach.yml", "--limit", ",".join(sorted(set(routers)))],
                  environment.codebase() / "ansible", env=ansible_env())


# --- Create ---


async def stage_validate(ctx: JobContext, record: InstanceRecord):
    render_env()
    spec = spec_of(record)
    _, findings = await service.validate(spec, prefix=record.id, exclude=record.id)
    for f in findings:
        ctx.log(f"{f.severity.upper()} {f.code} at {f.path or '/'}: {f.message}")
    errors = service.errors(findings)
    if errors:
        raise StageError("Validation failed: " + "; ".join(f"{f.code} ({f.path}): {f.message}" for f in errors))


async def stage_allocate(ctx: JobContext, record: InstanceRecord):
    if record.allocation:
        ctx.log("Already allocated")
        return
    spec = spec_of(record)
    with store.locked():
        allocation, findings = service.resolve(spec, prefix=record.id, exclude=record.id)
        if allocation is None:
            raise StageError("Allocation failed: " + "; ".join(f"{f.code}: {f.message}" for f in service.errors(findings)))
        with store.update_instance(record.id) as r:
            r.allocation = allocation
            r.base = version_ref(service.tfvars())
    for s in allocation.static_addresses:
        ctx.log(f"{s.router} {s.ip}: {s.use}")
    ctx.log(f"Vnets: {', '.join(f'{k} ({v})' for k, v in allocation.vnets.items()) or 'none'}")


def _serial(pem: str) -> Optional[str]:
    try:
        from cryptography import x509

        return str(x509.load_pem_x509_certificate(pem.encode()).serial_number)
    except Exception:
        return None


async def issue_certificates(ctx: JobContext, record: InstanceRecord, spec: SliceSpec):
    from services.ca_service import ca_service

    by_name = {h.name: h for h in record.allocation.hosts}
    directory = store.instance_dir(record.id)
    for tls in spec.tls:
        host = by_name.get(tls.host)
        if host is None:
            continue
        if any(c.host == host.hostname and c.names == tls.names and c.ca == tls.ca for c in record.certificates):
            continue
        result = await ca_service.issue_certificate(tls.names[0], tls.ca, sans=tls.names, not_after=settings.slice_certificate_validity)
        if not result.get("success") and "Invalid" in (result.get("message") or ""):
            ctx.log(f"{tls.ca} refused the SANs or validity ({result.get('message')}); issuing for {tls.names[0]} only")
            result = await ca_service.issue_certificate(tls.names[0], tls.ca)
        if not result.get("success"):
            raise StageError(f"{tls.ca} did not issue a certificate for {', '.join(tls.names)}: {result.get('message')}")
        cert_file = directory / f"{host.hostname}.crt"
        key_file = directory / f"{host.hostname}.key"
        store.write_text(cert_file, result["certificate"], mode=0o644)
        store.write_text(key_file, result["private_key"], mode=0o600)
        issued = IssuedCertificate(host=host.hostname, names=tls.names, ca=tls.ca, serial=_serial(result["certificate"]),
                                   cert_file=str(cert_file), key_file=str(key_file))
        with store.update_instance(record.id) as r:
            r.certificates = [c for c in r.certificates if c.host != host.hostname] + [issued]
        ctx.log(f"Issued {', '.join(tls.names)} for {host.hostname} by {tls.ca} (serial {issued.serial})")


async def write_ca_bundle(ctx: JobContext, record: InstanceRecord) -> Optional[str]:
    from services.ca_service import ca_service

    roots = await ca_service.get_ca_bundle()
    for ca, pem in roots.items():
        if pem is None:
            ctx.log(f"WARNING: could not read {ca}'s root certificate; slice hosts won't trust it")
    pems = [pem for pem in roots.values() if pem]
    if not pems:
        return None
    path = store.instance_dir(record.id) / "ca-bundle.pem"
    store.write_text(path, "".join(pems))
    return str(path)


async def stage_render(ctx: JobContext, record: InstanceRecord):
    env = render_env()
    spec = spec_of(record)
    await issue_certificates(ctx, record, spec)
    record = get_record(record.id)
    certs = {}
    for c in record.certificates:
        with open(c.cert_file) as f1, open(c.key_file) as f2:
            certs[c.host] = (f1.read(), f2.read())
    images = catalog.images()
    missing = [n for n in record.allocation.images if n not in images]
    if missing:
        raise StageError(f"Images no longer in the catalog: {', '.join(missing)}")
    team = crucible_team_id()
    store.write_text(instance_tfvars(record.id), render.dumps(render.instance_tfvars(record.allocation, images, env, certs, team)))
    write_images_tfvars(env, images_in_use(extra=record.allocation.images))
    ctx.log(f"Wrote {instance_tfvars(record.id)}" + (f"; guests join the base's Crucible team {team}" if team else ""))


async def stage_apply(ctx: JobContext, record: InstanceRecord):
    env = render_env()
    set_state(record.id, "provisioning")
    await apply_images(ctx, env, extra=record.allocation.images)
    module = environment.slice_module()
    await tf_init(ctx, module)
    await ensure_workspace(ctx, module, record.id)
    await converge(ctx, module, [f"-var-file={instance_tfvars(record.id)}"], workspace=record.id)
    await read_mgmt_ips(ctx, record.id)


async def read_mgmt_ips(ctx: JobContext, instance_id: str) -> Dict[str, str]:
    guests = (await tf_outputs(ctx, instance_id)).get("guests") or {}
    ips = {k: v["mgmt_ip"] for k, v in guests.items() if v.get("mgmt_ip")}
    with store.update_instance(instance_id) as r:
        r.mgmt_ips = ips
    return ips


def managed_hostnames(record: InstanceRecord) -> List[str]:
    a = record.allocation
    return [s.edge for s in a.sites] + [h.hostname for h in a.hosts if h.managed]


async def _port_open(ip: str, port: int = 22, timeout: float = 5) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def stage_wait(ctx: JobContext, record: InstanceRecord):
    wanted = managed_hostnames(record)
    deadline = asyncio.get_running_loop().time() + settings.slice_ssh_timeout
    while True:
        ips = get_record(record.id).mgmt_ips
        missing = [h for h in wanted if h not in ips]
        if missing:
            ctx.log(f"No management address yet for {', '.join(missing)}; refreshing")
            await ctx.run(["terraform", "apply", "-refresh-only", "-auto-approve", "-input=false", f"-var-file={instance_tfvars(record.id)}"],
                          environment.slice_module(), env=tf_env(record.id), check=False, quiet=True)
            ips = await read_mgmt_ips(ctx, record.id)
            missing = [h for h in wanted if h not in ips]
        down = [h for h in wanted if h in ips and not await _port_open(ips[h])]
        if not missing and not down:
            ctx.log(f"SSH is up on {len(wanted)} guests")
            return
        if asyncio.get_running_loop().time() > deadline:
            raise StageError(f"After {settings.slice_ssh_timeout}s: no management address for {missing or 'none'}, no SSH on {down or 'none'}")
        ctx.attempt(f"waiting for {len(missing) + len(down)} of {len(wanted)} guests")
        ctx.check_cancel()
        await asyncio.sleep(10)


def neighbors_of(record: InstanceRecord) -> Dict[str, list]:
    ctx = service.context()
    out = {}
    for site in record.allocation.sites:
        if site.routed:
            out[site.edge] = [{"ip": w.gateway, "asn": ctx.routers[w.router].asn, "router": w.router} for w in site.wans]
    return out


async def stage_configure(ctx: JobContext, record: InstanceRecord):
    set_state(record.id, "configuring")
    alloc = record.allocation
    attachments = [BaseAttachment(router=w.router, edge=s.edge, ip=w.ip, asn=s.asn) for s in alloc.sites if s.routed for w in s.wans]
    if attachments:
        with store.update_instance(record.id) as r:
            r.attachments = attachments
        ctx.log(f"Adding BGP neighbors on {', '.join(a.router for a in attachments)}")
        await attach_routers(ctx, [a.router for a in attachments])

    spec = spec_of(record)
    bundle = await write_ca_bundle(ctx, record)
    certs = {c.host: {"cert": c.cert_file, "key": c.key_file} for c in record.certificates}
    variables = render.ansible_vars(alloc, spec.role_vars, [d.domain for d in spec.dns.domains], bundle, certs, neighbors_of(record))
    variables["slice_mgmt_gateway"] = settings.slice_mgmt_gateway or ""
    variables["slice_mgmt_resolver"] = settings.slice_mgmt_resolver or ""
    key = str(environment.codebase() / ".ssh" / "terraform_key")
    store.write_text(inventory_file(record.id), render.inventory(alloc, get_record(record.id).mgmt_ips, key))
    store.write_text(vars_file(record.id), render.ansible_vars_yaml(variables), mode=0o600)
    # inventory/proxy.ini: the proxy variables (from the environment) that the base's plays use too
    proxy_vars = environment.codebase() / "ansible" / "inventory" / "proxy.ini"
    await ctx.run(["ansible-playbook", "-i", str(proxy_vars), "-i", str(inventory_file(record.id)),
                   "-e", f"@{vars_file(record.id)}", "slice.yml"],
                  environment.codebase() / "ansible", env=ansible_env())


def record_target(record: InstanceRecord, host_name: str) -> Optional[str]:
    """The address a DNS record for a slice host resolves to: its public address if it has one."""
    for h in record.allocation.hosts:
        if h.name == host_name:
            return h.public_ip or h.ip
    return None


def zone_of(fqdn: str, domains: List[str]) -> Optional[str]:
    return max((d for d in domains if fqdn == d or fqdn.endswith("." + d)), key=len, default=None)


async def stage_dns(ctx: JobContext, record: InstanceRecord):
    from services.dns_service import dns_service

    spec = spec_of(record)
    for dom in spec.dns.domains:
        if any(d.domain == dom.domain for d in get_record(record.id).domains):
            continue
        result = await dns_service.create_domain(dom.domain, hosting_server=dom.hosting_server, create_www=False, track=False)
        if not result.success:
            raise StageError(f"Could not create {dom.domain}: {result.message}")
        for w in result.warnings:
            ctx.log(f"WARNING: {w}")
        with store.update_instance(record.id) as r:
            r.domains.append(CreatedDomain(domain=dom.domain, hosting_server=dom.hosting_server, tld_server=result.tld_server))
        ctx.log(f"Created {dom.domain} on {dom.hosting_server}, delegated from {result.tld_server}")
    domains = {d.domain: d for d in spec.dns.domains}
    for rec in spec.dns.records:
        data = record_target(record, rec.host) if rec.host else rec.data
        if any(r.fqdn == rec.name and r.type == rec.type and r.data == data for r in get_record(record.id).records):
            continue
        zone = zone_of(rec.name, list(domains))
        name = "@" if rec.name == zone else rec.name[: -len(zone) - 1]
        server = domains[zone].hosting_server
        result = await dns_service.add_record(server, zone, name, rec.type, data, rec.ttl, track=False)
        if not result.success:
            raise StageError(f"Could not add {rec.name} {rec.type} {data}: {result.message}")
        with store.update_instance(record.id) as r:
            r.records.append(CreatedRecord(server=server, zone=zone, name=name, fqdn=rec.name, type=rec.type, data=data, ttl=rec.ttl))
        ctx.log(f"{rec.name} {rec.type} {data}")


async def stage_conditions(ctx: JobContext, record: InstanceRecord):
    from models.traffic import TrafficConfig
    from services.traffic_service import traffic_service

    spec = spec_of(record)
    base = service.context()
    for link_id, cond in spec.conditions.links.items():
        canonical = plan.canonical_link(base, link_id)
        if canonical is None:
            raise StageError(f"No link {link_id}")
        if any(c.link_id == canonical for c in get_record(record.id).conditions):
            continue
        config = TrafficConfig(latency_ms=cond.latency_ms, jitter_ms=cond.jitter_ms, bandwidth_mbps=cond.bandwidth_mbps,
                               packet_loss_percent=cond.packet_loss_percent)
        ok, message = await traffic_service.apply_link_traffic_config(canonical, config)
        if not ok:
            raise StageError(message)
        with store.update_instance(record.id) as r:
            r.conditions.append(LinkConditionApplied(link_id=canonical, origin="spec", **cond.model_dump()))
        ctx.log(f"{canonical}: {cond.model_dump(exclude_none=True)}")


async def _retrying(check, attempts: int = 3, delay: int = 10):
    """Run a check a few times: BGP sessions and DNS caches may need a moment."""
    for i in range(attempts):
        ok, detail = await check()
        if ok or i == attempts - 1:
            return ok, detail
        await asyncio.sleep(delay)


async def stage_check(ctx: JobContext, record: InstanceRecord):
    """Each edge reaches its base router; one host per site resolves a base name and reaches an
    internet host; each published and internet address answers from its base router; and each
    certificate is served over TLS to a site host by name."""
    alloc = record.allocation
    ips = record.mgmt_ips
    base = service.context()
    problems = []
    tld = sorted(base.tlds)[0] if base.tlds else None
    internet_hosts = [h for h in alloc.hosts if h.site is None]
    target = internet_hosts[0].ip if internet_hosts else next((s.ip for s in base.dns.values() if s.role == "resolver"), None)

    for site in alloc.sites:
        for wan in site.wans:
            async def edge_ping(ip=ips.get(site.edge), gw=wan.gateway):
                code, out = await ssh(ip, f"ping -c 2 -W 2 {gw}")
                return code == 0, out
            ok, detail = await _retrying(edge_ping)
            ctx.log(f"{site.edge} -> {wan.router} {wan.gateway}: {'ok' if ok else 'FAILED'}")
            if not ok:
                problems.append(f"{site.edge} can't reach {wan.router} ({wan.gateway})")
        probe = next((h for h in alloc.hosts if h.site == site.name and h.managed and h.os_family != "windows" and h.hostname in ips), None)
        if probe is None:
            ctx.log(f"Site {site.name}: no Linux host to probe from")
            continue
        if tld:
            async def resolve_check(ip=ips[probe.hostname]):
                code, out = await ssh(ip, f"getent hosts ns1.{tld}")
                return code == 0, out
            ok, detail = await _retrying(resolve_check)
            ctx.log(f"{probe.hostname} resolves ns1.{tld}: {'ok ' + detail if ok else 'FAILED'}")
            if not ok:
                problems.append(f"{probe.hostname} can't resolve ns1.{tld}")
        if target:
            async def reach(ip=ips[probe.hostname]):
                code, out = await ssh(ip, f"ping -c 2 -W 2 {target}")
                return code == 0, out
            ok, _ = await _retrying(reach)
            ctx.log(f"{probe.hostname} -> {target}: {'ok' if ok else 'FAILED'}")
            if not ok:
                problems.append(f"{probe.hostname} can't reach {target}")
        for cert in record.certificates:
            for name in cert.names:
                if not any(r.fqdn == name for r in record.records):
                    continue
                url = f"https://{name}/"
                script = f"python3 -c \"import urllib.request; print(urllib.request.urlopen('{url}', timeout=10).status)\""
                async def tls(ip=ips[probe.hostname], script=script):
                    code, out = await ssh(ip, script)
                    return code == 0, out
                ok, detail = await _retrying(tls)
                ctx.log(f"{probe.hostname} GET {url}: {'ok ' + detail if ok else 'FAILED ' + detail[-200:]}")
                if not ok:
                    problems.append(f"{probe.hostname} can't fetch {url} over TLS")

    for h in alloc.hosts:
        if not h.public_ip:
            continue
        site = next((s for s in alloc.sites if s.name == h.site), None)
        if site is not None and site.routed:
            router = site.wans[0].router
        else:
            router = h.router or (site.wans[0].router if site else None)
        router_ip = await base_mgmt_ip(router) if router else None
        if not router_ip:
            problems.append(f"No management address for base router {router}")
            continue
        async def answer(ip=router_ip, addr=h.public_ip):
            code, out = await ssh(ip, f"ping -c 2 -W 2 {addr}")
            return code == 0, out
        ok, _ = await _retrying(answer)
        ctx.log(f"{router} -> {h.hostname} {h.public_ip}: {'ok' if ok else 'FAILED'}")
        if not ok:
            problems.append(f"{h.hostname} ({h.public_ip}) doesn't answer from {router}")

    if problems:
        raise StageError("Checks failed: " + "; ".join(problems))


async def statuses(instance_id: str) -> Dict[str, str]:
    from services.proxmox_client import proxmox_client

    try:
        containers = await asyncio.to_thread(proxmox_client.get_instance_containers, instance_id)
    except Exception:
        return {}
    return {c["name"]: c.get("status", "unknown") for c in containers}


async def stage_ready(ctx: JobContext, record: InstanceRecord):
    set_state(record.id, "ready")
    record = get_record(record.id)
    built = manifest.build(record, await statuses(record.id))
    with store.update_instance(record.id) as r:
        r.manifest = built
    ctx.log(f"Ready: {len(built.sites)} sites, {len(built.hosts)} hosts, {len(built.dns)} DNS records")


CREATE = {
    "validate": stage_validate, "allocate": stage_allocate, "render": stage_render, "apply": stage_apply,
    "wait": stage_wait, "configure": stage_configure, "dns": stage_dns, "conditions": stage_conditions,
    "check": stage_check, "ready": stage_ready,
}


def destroy_started(record: InstanceRecord) -> bool:
    """Whether a destroy job has run for the instance: once it has, it may have removed
    certificates, records and attachments, so a create can't resume; the destroy must finish."""
    for job_id in record.jobs:
        job = store.get_job(job_id)
        if job and job.kind == "destroy_instance" and job.state != "queued":
            return True
    return False


async def run_create(ctx: JobContext, job: JobRecord):
    record = get_record(job.target)
    if record.state in ("destroying", "destroyed"):
        raise StageError(f"Instance {record.id} is {record.state}")
    if destroy_started(record):
        raise StageError(f"Instance {record.id} has been partly destroyed; retry its destroy job instead")
    if record.state in ("pending", "failed"):
        set_state(record.id, "provisioning")
    for name in CREATE_STAGES:
        record = get_record(job.target)
        if name in record.stages_done:
            continue
        ctx.stage(name)
        await CREATE[name](ctx, record)
        with store.update_instance(record.id) as r:
            if name not in r.stages_done:
                r.stages_done.append(name)
    if "player" in job.stages:
        ctx.stage("player")
        await stage_player(ctx)


def create_failed(job: JobRecord, message: str):
    if store.get_instance(job.target):
        set_state(job.target, "failed", f"{job.stage or 'create'}: {message}")


# --- Destroy ---


async def destroy_revoke(ctx: JobContext, record: InstanceRecord):
    from services.ca_service import ca_service

    for cert in list(record.certificates):
        if cert.serial:
            result = await ca_service.revoke_certificate(cert.ca, cert.serial)
            ctx.log(f"Revoke {cert.serial} on {cert.ca}: {result.get('message')}")
        with store.update_instance(record.id) as r:
            r.certificates = [c for c in r.certificates if c.host != cert.host]


async def destroy_dns(ctx: JobContext, record: InstanceRecord):
    from services.dns_service import dns_service

    for rec in reversed(list(record.records)):
        ok, message = await dns_service.revert_record({"server": rec.server, "zone": rec.zone, "name": rec.name, "type": rec.type, "data": rec.data, "ttl": rec.ttl})
        ctx.log(message)
        with store.update_instance(record.id) as r:
            r.records = [x for x in r.records if x != rec]
    for dom in reversed(list(get_record(record.id).domains)):
        ok, message = await dns_service.revert_domain({"domain": dom.domain, "hosting_server": dom.hosting_server, "tld_server": dom.tld_server})
        ctx.log(message)
        with store.update_instance(record.id) as r:
            r.domains = [d for d in r.domains if d.domain != dom.domain]


async def destroy_conditions(ctx: JobContext, record: InstanceRecord):
    from services import instance_actions

    await instance_actions.release_links(record.id, log=ctx.log)


async def destroy_detach(ctx: JobContext, record: InstanceRecord):
    routers = [a.router for a in record.attachments]
    if not routers:
        return
    with store.update_instance(record.id) as r:
        r.attachments = []
    ctx.log(f"Removing BGP neighbors from {', '.join(routers)}")
    await attach_routers(ctx, routers)


async def destroy_resources(ctx: JobContext, record: InstanceRecord):
    module = environment.slice_module()
    await tf_init(ctx, module)
    if record.id not in await tf_workspaces(ctx, module):
        ctx.log("No Terraform workspace: nothing was applied")
        return
    if not instance_tfvars(record.id).exists():
        if record.allocation is None:
            ctx.log("No allocation: nothing to destroy")
            return
        env = render_env()
        store.write_text(instance_tfvars(record.id), render.dumps(render.instance_tfvars(record.allocation, catalog.images(), env,
                                                                                         crucible_team_id=crucible_team_id())))
    env = tf_env(record.id)
    # As for the base: the Crucible provider's refresh can panic, and its delete path works
    crucible = ["-refresh=false"] if registered_in_crucible(record.id) else []
    if crucible:
        ctx.log("The instance's guests are registered in Crucible: destroying with -refresh=false")
    for i in range(1, 4):
        ctx.attempt(f"destroy attempt {i}/3")
        # As for the base, the first attempt doesn't refresh: a destroy needs no fresh state, and a refresh
        # waits on every VM's guest agent for its IPs (agent.timeout, 15m), which a VM without an agent
        # turns into the whole timeout. Later attempts refresh, which drops anything deleted outside Terraform.
        extra = crucible or (["-refresh=false"] if i == 1 else [])
        rc, _ = await ctx.run(["terraform", "destroy", "-auto-approve", "-input=false", *extra, f"-var-file={instance_tfvars(record.id)}",
                               f"-parallelism={environment.parallelism('apply')}"], module, env=env, check=False)
        _, state = await ctx.run(["terraform", "state", "list"], module, env=env, check=False, quiet=True)
        if rc == 0 and not state.strip():
            ctx.attempt(None)
            return
    raise StageError("terraform destroy left resources in the workspace; see the log, then retry the job")


async def destroy_workspace(ctx: JobContext, record: InstanceRecord):
    module = environment.slice_module()
    if record.id in await tf_workspaces(ctx, module):
        await delete_workspace(ctx, module, record.id)
    for path in (instance_tfvars(record.id), inventory_file(record.id), vars_file(record.id)):
        if path.exists():
            path.unlink()


async def destroy_images(ctx: JobContext, record: InstanceRecord):
    if not images_tfvars().exists():
        ctx.log("No images were rendered: nothing to remove")
        return
    await apply_images(ctx, render_env(), exclude=record.id)


async def destroy_free(ctx: JobContext, record: InstanceRecord):
    from services import rollout  # it imports the runner, which imports this module

    rollout.forget(record.id)  # a destroyed instance is no longer the draft's, however it was destroyed
    with store.update_instance(record.id) as r:
        r.state = "destroyed"
        r.error = None
        r.mgmt_ips = {}
        r.stages_done = []
        if r.manifest:
            r.manifest.state = "destroyed"
    d = store.data_dir() / "instances" / record.id
    if d.exists():
        shutil.rmtree(d)
    events.publish_instance(get_record(record.id))
    ctx.log("Allocations freed")


DESTROY = {
    "revoke": destroy_revoke, "dns": destroy_dns, "conditions": destroy_conditions, "detach": destroy_detach,
    "destroy": destroy_resources, "workspace": destroy_workspace, "images": destroy_images, "free": destroy_free,
}


async def run_destroy(ctx: JobContext, job: JobRecord):
    record = get_record(job.target)
    if record.state == "destroyed":
        ctx.log("Already destroyed")
        return
    set_state(record.id, "destroying")
    if "player" in job.stages and job.resume_from in (None, "player"):
        ctx.stage("player")
        await stage_player(ctx, exclude=record.id)
    for name in DESTROY_STAGES:
        ctx.stage(name)
        await DESTROY[name](ctx, get_record(job.target))


def destroy_failed(job: JobRecord, message: str):
    if store.get_instance(job.target):
        set_state(job.target, "failed", f"destroy ({job.stage}): {message}")


# --- Plan ---


async def run_plan(ctx: JobContext, job: JobRecord):
    """Allocation preview, terraform plan in a scratch workspace, and the capacity required."""
    stored = store.get_slice(job.slice.slug, job.slice.version)
    if stored is None:
        raise StageError(f"No slice {job.slice.slug} version {job.slice.version}")
    spec = stored[1]
    prefix = (job.result or {}).get("prefix") or spec.prefix
    ctx.stage("validate")
    env = render_env()
    allocation, findings = await service.validate(spec, prefix=prefix)
    result = {"prefix": prefix, "findings": [f.model_dump() for f in findings]}
    ctx.update(result=result)
    for f in findings:
        ctx.log(f"{f.severity.upper()} {f.code} at {f.path or '/'}: {f.message}")
    if allocation is None:
        raise StageError("Validation failed: " + "; ".join(f"{f.code}: {f.message}" for f in service.errors(findings)))
    ctx.stage("allocate")
    result["allocation"] = allocation.model_dump(mode="json")
    result["capacity_required"] = plan.required_capacity(allocation)
    ctx.update(result=result)
    ctx.stage("render")
    scratch = store.data_dir() / "jobs" / f"{job.id}.tfvars.json"
    store.write_text(scratch, render.dumps(render.instance_tfvars(allocation, catalog.images(), env, crucible_team_id=crucible_team_id())))
    ctx.stage("plan")
    module = environment.slice_module()
    workspace = f"plan-{job.id}"
    await tf_init(ctx, module)
    await ensure_workspace(ctx, module, workspace)
    try:
        rc, out = await ctx.run(["terraform", "plan", "-input=false", "-detailed-exitcode", f"-var-file={scratch}",
                                 f"-parallelism={environment.parallelism('plan')}"],
                                module, env=tf_env(workspace), check=False)
        summary = next((line.strip() for line in out.splitlines() if line.strip().startswith(("Plan:", "No changes."))), None)
        counts = re.search(r"(\d+) to add, (\d+) to change, (\d+) to destroy", summary or "")
        result["plan"] = {
            "exit_code": rc,
            "summary": summary,
            "add": int(counts.group(1)) if counts else 0,
            "change": int(counts.group(2)) if counts else 0,
            "destroy": int(counts.group(3)) if counts else 0,
        }
        ctx.update(result=result)
        if rc == 1:
            raise StageError("terraform plan failed; see the log")
    finally:
        # The scratch workspace never had resources applied; -force drops its empty state.
        await ctx.run(["terraform", "workspace", "select", "default"], module, env=tf_env(), check=False, quiet=True)
        await ctx.run(["terraform", "workspace", "delete", "-force", workspace], module, env=tf_env(), check=False, quiet=True)
        if scratch.exists():
            os.unlink(scratch)


KINDS["create_instance"] = run_create
KINDS["destroy_instance"] = run_destroy
KINDS["plan_slice"] = run_plan
FAILURE_HOOKS["create_instance"] = create_failed
FAILURE_HOOKS["destroy_instance"] = destroy_failed
