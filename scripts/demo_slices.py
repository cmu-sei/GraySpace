#!/usr/bin/env python3
"""A narrated demo of slices (docs/slices.md): from a 50-line spec to an enterprise on the
simulated internet.

    task demo                       # interactive: Enter advances
    task demo -- --auto             # timed, hands-free (a screen recording)
    task demo -- --offline          # acts 1-6 only, no running services
    task demo -- --clean            # remove what the live act staged

Acts 1-6 run the backend's own allocator, validator and renderer in this process against the
base the API serves (terraform/terraform.tfvars.json, else the golden grayspace/2026.11), so they
need no Proxmox and no services. Act 7 uses the simulated Aspire stack (task dev): it stores the
slices through the Terrain API, then stages instances in the API's data directory so they appear
on the map and in /api/v1/instances. validate, allocate and render are real there too; the stages
that need Proxmox (apply, wait, configure, dns, conditions, check) are marked as simulated. Act 7
refuses to stage anything unless the API reports DEV_SIMULATE_DEPLOYMENT.
"""
import argparse
import copy
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "grayspace-web" / "backend"
EXAMPLES = ROOT / "examples" / "slices"
OUT = Path(tempfile.gettempdir()) / "grayspace-demo"
SIM_DATA = ROOT / "aspire" / ".data" / "backend"
MARKER = "demo-staged.json"
STAGED = ["meridian-bank", "routed-campus", "information-environment"]

# --- Presentation ---

COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code):
    return (lambda s: f"\033[{code}m{s}\033[0m") if COLOR else (lambda s: str(s))


bold, dim, red, green, yellow, blue, magenta, cyan = (_c(c) for c in ("1", "2", "31", "32", "33", "34", "35", "36"))
MODE = {"pace": "interactive"}


def strip(s):
    return re.sub(r"\033\[[0-9;]*m", "", s)


def out(line="", delay=0.015):
    print(line, flush=True)
    if MODE["pace"] != "fast":
        time.sleep(delay)


def say(text):
    """Narration: wrapped, dim."""
    import textwrap

    for line in textwrap.wrap(text, 96):
        out(dim(line), 0.04)


def pause(seconds=2.5):
    if MODE["pace"] == "fast":
        return
    if MODE["pace"] == "auto":
        time.sleep(seconds)
        return
    try:
        input(dim("  ⏎ "))
        sys.stdout.write("\033[1A\033[2K" if COLOR else "")
    except EOFError:
        MODE["pace"] = "auto"


ACT = {"n": 0}


def act(title, subtitle):
    ACT["n"] += 1
    out()
    out(bold(magenta(f"━━ {ACT['n']} ━━ {title} ")) + magenta("━" * max(3, 80 - len(title))))
    out(dim(subtitle))
    out()


def bar(used, total, width=18):
    filled = round(width * used / total) if total else 0
    return green("█" * filled) + dim("░" * (width - filled))


def show_yaml(path: Path):
    lines = [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]
    for line in lines:
        m = re.match(r"^(\s*-?\s*)([\w.-]+)(:)(.*)$", line)
        if m:
            value = re.sub(r"(\{|\}|\[|\])", lambda x: dim(x.group(1)), m.group(4))
            out(f"  {dim(m.group(1))}{cyan(m.group(2))}{m.group(3)}{value}", 0.03)
        else:
            out(f"  {line}", 0.03)
    return len(lines)


def findings_table(findings, limit=12):
    """Findings, repeated ones (one per counted host) folded into one line."""
    grouped = OrderedDict()
    for f in findings:
        grouped.setdefault((f.severity, f.code, f.path), []).append(f)
    for shown, ((severity, code, path), group) in enumerate(grouped.items()):
        if shown == limit:
            out(dim(f"  … and {len(grouped) - limit} more"))
            break
        f = group[0]
        mark = red("✗") if severity == "error" else yellow("!")
        times = dim(f" ×{len(group)}") if len(group) > 1 else ""
        out(f"  {mark} {bold(code):<{32 if COLOR else 22}} {dim(path)}{times}", 0.08)
        out(f"      {f.message}", 0.03)
        if f.suggestion is not None:
            s = f.suggestion if isinstance(f.suggestion, str) else json.dumps(f.suggestion)
            out(f"      {green('→ try')} {green(s)}", 0.03)


# --- The backend, in this process ---


def tfvars_path() -> Path:
    """The base the simulated API serves (aspire/GraySpace.AppHost: GraySpace:Tfvars unset)."""
    deployed = ROOT / "terraform" / "terraform.tfvars.json"
    return deployed if deployed.is_file() else ROOT / "tests" / "golden" / "tfvars-full.json"


def bootstrap(data_dir: Path):
    for key, value in {
        "ENVIRONMENT_ID": "demo",
        "PROMETHEUS_URL": "http://prometheus.invalid:9090",
        "PROXMOX_API_HOST": "proxmox.invalid",
        "PROXMOX_NODE": "pve",
        "TFVARS_PATH": str(tfvars_path()),
        "BASES_PATH": str(ROOT / "bases"),
        "SCRIPTS_PATH": str(ROOT / "scripts"),
        "CODEBASE_PATH": str(ROOT),
    }.items():
        os.environ.setdefault(key, value)
    os.environ["DATA_PATH"] = str(data_dir)
    os.chdir(tempfile.gettempdir())  # config.py reads .env from the working directory
    sys.path.insert(0, str(BACKEND))


def load_spec(raw):
    from models.slices import SliceSpec

    return SliceSpec.model_validate(raw)


def read_example(name):
    import yaml

    return yaml.safe_load((EXAMPLES / f"{name}.yaml").read_text())


class World:
    """The base and the instances this demo has 'built', for plan.resolve."""

    def __init__(self):
        from services.slices import base, catalog

        self.tfvars = json.loads(tfvars_path().read_text())
        self.ctx = base.from_tfvars(self.tfvars)
        self.images = catalog.images()
        self.roles = catalog.roles()
        self.instances = []

    def resolve(self, spec, prefix=None, alone=False):
        """alone: as if no instance were live."""
        from services.slices import plan

        taken = plan.Taken() if alone else plan.Taken.from_instances(self.instances)
        return plan.resolve(spec, self.ctx, self.images, self.roles, taken, prefix=prefix)

    def record(self, spec, alloc, state="ready"):
        from models.instances import (
            CreatedDomain,
            CreatedRecord,
            InstanceRecord,
            SliceRef,
        )
        from services.base_service import version_ref

        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        rec = InstanceRecord(id=alloc.prefix, slice=SliceRef(slug=spec.slug, version=spec.version()), state=state,
                             created=now, updated=now, allocation=alloc, base=version_ref(self.tfvars))
        hosting = {d.domain: d.hosting_server for d in spec.dns.domains}
        tlds = self.ctx.tlds
        for d in spec.dns.domains:
            rec.domains.append(CreatedDomain(domain=d.domain, hosting_server=d.hosting_server, tld_server=tlds.get(d.domain.rsplit(".", 1)[-1])))
        for r in spec.dns.records:
            zone = max((d for d in hosting if r.name == d or r.name.endswith("." + d)), key=len)
            data = next((h.public_ip or h.ip for h in alloc.hosts if h.name == r.host), None) if r.host else r.data
            rec.records.append(CreatedRecord(server=hosting[zone], zone=zone, name="@" if r.name == zone else r.name[: -len(zone) - 1],
                                             fqdn=r.name, type=r.type, data=data, ttl=r.ttl))
        return rec

    def static_use(self, router):
        """{ip: use} on a router's customer network: the base's, then each instance's."""
        used = {ip: "base" for ip in self.ctx.used_addresses.get(router, set())}
        for inst in self.instances:
            for s in inst.allocation.static_addresses:
                if s.router == router:
                    used[s.ip] = s.use
        return used


def capacity_line(alloc):
    from services.slices import plan

    need = plan.required_capacity(alloc)
    return (f"{need['guests']} guests · {need['cores']} cores · {need['memory_mib'] / 1024:.1f} GiB memory · "
            f"{need['disk_gib']} GiB disk")


def customer_net(world, router_name, highlight=()):
    r = world.ctx.routers[router_name]
    used = world.static_use(router_name)
    static = world.ctx.static_range(router_name)
    out(f"  {bold(router_name)} {dim(f'({r.isp} AS{r.asn}, {r.location})')}   customer network {cyan(r.customer_net)} on vnet {r.customer_bridge}")
    out(f"    {'.1':<6} {r.hostname:<20} {dim('the core router, gateway for everything below')}")
    for ip in static:
        use = used.get(ip)
        if not use or use == "base":
            continue
        who, _, what = use.partition(" ")
        tag = magenta(who) if who.split("-")[0] in highlight else yellow(who)
        out(f"    {'.' + ip.rsplit('.', 1)[1]:<6} {tag:<{29 if COLOR else 20}} {dim(what or 'internet host')}", 0.06)
    free = [ip for ip in static if ip not in used]
    taken = len(static) - len(free)
    out(f"    {dim(f'{len(free)} static addresses free')}  {bar(taken, len(static))} {taken}/{len(static)}")
    out(f"    {dim('.' + str(world.ctx.dhcp_start) + '–.254')}  {dim('DHCP for hosts on the customer network')}")


# --- Acts ---


def act_base(world):
    act("The internet you already have", f"A shipped core: {world.tfvars.get('base', {}).get('name', 'the deployed core')}, "
        "deployed by task deploy, fixed while slices run")
    routers = world.ctx.routers.values()
    isps = Counter(r.isp for r in routers)
    attach = [r for r in routers if r.customer_net]
    free = sum(len(world.ctx.free_static(r.hostname, set())) for r in attach)
    out(f"  {bold(len(isps))} ISPs   {bold(len(world.ctx.routers))} routers   {bold(len(world.ctx.links))} links   "
        f"{bold(len({r.location for r in routers}))} cities   {bold(len(world.ctx.dns))} DNS servers   {bold(len(world.ctx.cas))} CAs")
    out()
    top = max(isps.values())
    for isp, n in isps.most_common():
        asn = next(r.asn for r in routers if r.isp == isp)
        out(f"  {isp:<16} {dim(f'AS{asn:<6}')} {blue('▇' * round(30 * n / top))} {n}", 0.025)
    out()
    say(f"BGP between ISPs, OSPF inside them, real DNS from the root down, four certificate authorities. "
        f"{len(attach)} routers have a customer network where something can attach: {free} free static addresses in all.")
    say("What it doesn't have is anyone living on it: no companies, no users, no web sites of an exercise's own.")
    pause()


def act_spec(world):
    act("An enterprise in fifty lines", "examples/slices/meridian-bank.yaml: what the exercise needs, not how to build it")
    n = show_yaml(EXAMPLES / "meridian-bank.yaml")
    out()
    say(f"{n} lines. Two offices, a public web site with a real certificate and DNS name, and 45 workstations "
        "for GHOSTS to drive. No IP addresses for the internet side, no router names, no VLANs, no Terraform.")
    pause()


def act_allocate(world):
    act("GraySpace decides the how", "plan.resolve: the same code task slice:plan, the API and the create job run")
    spec = load_spec(read_example("meridian-bank"))
    t0 = time.perf_counter()
    alloc, findings = world.resolve(spec)
    ms = (time.perf_counter() - t0) * 1000
    assert alloc is not None, findings
    out(f"  {green('✓')} {bold(spec.slug)} {dim(spec.version()[:23] + '…')}  resolved in {ms:.0f} ms, {len(findings)} findings")
    out()
    for s in alloc.sites:
        w = s.wans[0]
        out(f"  site {bold(s.name):<{18 if COLOR else 10}} {magenta(s.edge)} on {cyan(w.router)} {dim(f'({w.isp}, {w.location})')} WAN {w.ip}")
        for g in s.segments:
            members = [h for h in alloc.hosts if h.segment == g.name]
            out(f"       {g.name:<10} {g.cidr:<18} {dim('vnet ' + g.vnet)}  {len(members)} hosts", 0.05)
    out()
    web = next(h for h in alloc.hosts if h.name == "web01")
    pools = Counter(h.labels.get("ghosts.pool") for h in alloc.hosts if h.labels.get("ghosts.pool"))
    out(f"  {magenta(web.hostname)}  {web.ip} inside, {bold(web.public_ip)} on the internet (1:1 NAT on the edge), "
        f"https://www.meridian-bank.com from {spec.tls[0].ca}")
    out(f"  {magenta('mb-ws-01')} … {magenta('mb-ws-40')}, {magenta('mb-br-ws-01')} … {magenta('mb-br-ws-05')}  "
        + ", ".join(f"pool {k}: {v}" for k, v in pools.items()))
    out(f"  resolver {alloc.sites[0].resolver} (the core resolver nearest Chicago)   {capacity_line(alloc)}")
    out()
    world.instances.append(world.record(spec, alloc))
    customer_net(world, "lum-chi", highlight=("mb",))
    out()
    say("Lumen in Chicago resolved to lum-chi, the router with the most free addresses that matches. The HQ edge "
        "took .2, the published web server .3. Nothing in the core changes: lum-chi already routes that network.")
    pause()
    return spec, alloc


def act_render(world, spec, alloc):
    from services.slices import render

    act("What you didn't have to write", "render.py: the Terraform input and the Ansible inventory and vars the create job hands on")
    OUT.mkdir(parents=True, exist_ok=True)
    env = render.RenderEnv(environment_id="demo", filestore="local")
    mgmt = {h: f"10.0.9.{i + 10}" for i, h in enumerate(alloc.hostnames())}
    files = {
        f"{alloc.prefix}.tfvars.json": render.dumps(render.instance_tfvars(alloc, world.images, env)),
        f"{alloc.prefix}.vars.json": render.dumps(render.ansible_vars(alloc, spec.role_vars, [d.domain for d in spec.dns.domains],
                                                                     None, {}, {})),
        f"{alloc.prefix}.ini": render.inventory(alloc, mgmt, str(ROOT / ".ssh" / "terraform_key")),
    }
    total = 0
    for name, text in files.items():
        (OUT / name).write_text(text)
        lines = text.count("\n")
        total += lines
        out(f"  {green('✓')} {OUT / name}  {bold(lines)} lines", 0.2)
    tf = json.loads(files[f"{alloc.prefix}.tfvars.json"])
    out()
    edge = f"{alloc.prefix}-hq-edge"
    excerpt = json.dumps({edge: tf["edges"][edge]}, indent=2).splitlines()
    for line in excerpt[:24]:
        out(dim("    │ ") + line, 0.02)
    if len(excerpt) > 24:
        out(dim(f"    │ … {len(excerpt) - 24} more lines for this edge"))
    out()
    say(f"{total} generated lines from {len((EXAMPLES / 'meridian-bank.yaml').read_text().splitlines())} written ones: "
        f"{len(tf['vnets'])} vnets, {len(tf['edges'])} edge routers, {len(tf['lxc_hosts'])} containers, each with its "
        "simulated NIC, management NIC, gateway, resolver and Proxmox tags. The create job applies it in its own Terraform "
        "workspace and configures it with Ansible.")
    pause()


def act_mistakes(world):
    act("Mistakes, caught before anything is built", "Validation never changes the spec: it returns findings, with a fix where there is one")
    raw = read_example("meridian-bank")
    broken = copy.deepcopy(raw)
    edits = [
        ("sites[1].attach", "{isp: Comcast, location: Tokyo}", lambda b: b["sites"][1].update(attach={"isp": "Comcast", "location": "Tokyo"})),
        ("sites[0].segments[1].cidr", "10.0.5.0/24", lambda b: b["sites"][0]["segments"][1].update(cidr="10.0.5.0/24")),
        ("hosts[1].image", "ubuntu-24.04-lxc", lambda b: b["hosts"][1].update(image="ubuntu-24.04-lxc")),
        ("hosts[+]", "news: {internet: Arelion/Ashburn, image: ghosts-pandora, roles: [web]}",
         lambda b: b["hosts"].append({"name": "news", "internet": {"isp": "Arelion", "location": "Ashburn"}, "image": "ghosts-pandora", "roles": ["web"]})),
        ("dns.records[0].host", "ws", lambda b: b["dns"]["records"][0].update(host="ws")),
        ("role_vars.ghosts-client.api_url", "(deleted)", lambda b: b["role_vars"]["ghosts-client"].pop("api_url")),
    ]
    out(dim("  Six plausible edits to the spec:"))
    for path, value, apply in edits:
        apply(broken)
        out(f"    {yellow('~')} {path:<34} {value}", 0.15)
    out()
    _, findings = world.resolve(load_spec(broken), alone=True)
    findings_table(findings)
    out()
    say("Each finding points at the field, says why, and where it can, says what would work: the GraySpace web UI "
        "and GHOSTS show the same findings before anyone presses build.")
    pause()


def act_tenants(world):
    act("Many tenants, one internet", "Instances share the core: addresses, ASNs, prefixes, hostnames and DNS names are allocated across all of them")
    for name in ("routed-campus", "information-environment"):
        spec = load_spec(read_example(name))
        alloc, findings = world.resolve(spec)
        assert alloc is not None, findings
        world.instances.append(world.record(spec, alloc))
        out(f"  {green('✓')} {bold(spec.prefix):<{13 if COLOR else 4}} {spec.slug:<26} {dim(spec.description)}", 0.2)
        for s in alloc.sites:
            if s.routed:
                peers = " and ".join(f"{w.router} (AS{w.asn})" for w in s.wans)
                out(f"       {magenta(s.edge)} announces {bold(s.public_prefix)} as AS{s.asn}, eBGP to {peers}")
        for h in alloc.hosts:
            if h.router:
                out(f"       {magenta(h.hostname)} at {bold(h.ip)} on {h.router}: {h.env.get('SITE_NAME') or h.image}")
    out()
    customer_net(world, "lum-chi", highlight=("mb", "rc"))
    out()
    say("The routed campus is dual-homed: its first WAN is on lum-chi too, so it took .4, the next free address. "
        "Its public /24 came out of Lumen's block, and its private ASN is the first free one.")
    pause()

    raw = read_example("meridian-bank")
    out()
    out(bold("  A second Meridian Bank?"))
    _, findings = world.resolve(load_spec(raw))
    findings_table([f for f in findings if f.code in ("PREFIX_TAKEN", "DNS_NAME_TAKEN")], limit=3)
    out()
    _, findings = world.resolve(load_spec(raw), prefix="mb2")
    out(f"  {dim('With PREFIX=mb2:')}")
    findings_table(findings, limit=3)
    out()
    say("Hostnames move with the prefix, but meridian-bank.com is one name on one internet. A second copy needs "
        "its own domain, or none.")
    pause()

    out()
    out(bold("  How many branch banks fit in Chicago? (the spec without DNS and TLS, one copy per prefix)"))
    lean = copy.deepcopy(raw)
    lean["dns"], lean["tls"] = {}, []
    spec = load_spec(lean)
    static = len(world.ctx.static_range("lum-chi"))
    for i in range(2, 30):
        prefix = f"bank{i}"
        alloc, findings = world.resolve(spec, prefix=prefix)
        if alloc is None:
            out()
            findings_table([f for f in findings if f.severity == "error"], limit=2)
            suggestion = next((f.suggestion for f in findings if f.code == "NO_STATIC_ADDRESSES" and f.suggestion), None)
            if suggestion:
                lean["sites"][0]["attach"] = suggestion
                alloc, findings = world.resolve(load_spec(lean), prefix=prefix)
                if alloc is not None:
                    world.instances.append(world.record(spec, alloc))
                    hq = alloc.sites[0].wans[0]
                    out(f"  {green('✓')} {prefix:<7} took the suggestion: HQ on {cyan(hq.router)} ({hq.isp}) at {hq.ip}")
            break
        world.instances.append(world.record(spec, alloc))
        used = len(world.static_use("lum-chi")) - len(world.ctx.used_addresses.get("lum-chi", set()))
        hq = alloc.sites[0]
        pub = next(h.public_ip for h in alloc.hosts if h.public_ip)
        out(f"  {green('✓')} {prefix:<7} {magenta(hq.edge):<{24 if COLOR else 15}} {hq.wans[0].ip:<16} web01 → {pub:<16} "
            f"lum-chi {bar(used, static)} {used}/{static}", 0.25 if MODE["pace"] != "fast" else 0)
    out()
    total = sum(len(i.allocation.hosts) + len(i.allocation.sites) for i in world.instances)
    say(f"{len(world.instances)} instances, {total} guests, no address used twice, and the allocator said where to go when Chicago's "
        "Lumen router filled up. None of these are stored: the live act builds three of them.")
    pause()


# --- Act 7: the simulated stack ---


class Api:
    def __init__(self, url, token):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method, path, body=None, timeout=10):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode()
                return resp.status, json.loads(text) if text else None
        except urllib.error.HTTPError as e:
            text = e.read().decode()
            try:
                return e.code, json.loads(text)
            except ValueError:
                return e.code, text

    def show(self, method, path, status):
        colour = green if status < 300 else red
        out(f"  {dim(method):<{14 if COLOR else 6}} {path:<52} {colour(status)}", 0.1)


def probe(api):
    """The API's capabilities if it answers and is a simulated deployment, else (None, why)."""
    try:
        status, caps = api.call("GET", "/api/v1/capabilities", timeout=3)
    except (urllib.error.URLError, OSError) as e:
        return None, f"no API at {api.url} ({getattr(e, 'reason', e)}); start it with task dev"
    if status != 200:
        return None, f"{api.url}/api/v1/capabilities answered {status}: {caps}"
    if caps.get("jobs") or "DEV_SIMULATE_DEPLOYMENT" not in (caps.get("jobs_unavailable_reason") or ""):
        return None, f"{api.url} is a real deployment: build instances there with task slice:up, not this demo"
    return caps, None


def clean(api, data_dir: Path, quiet=False):
    """Remove the instances a previous run staged, and the slices it stored."""
    from services.slices import store

    marker = data_dir / MARKER
    if not marker.exists():
        if not quiet:
            out(dim(f"  Nothing staged in {data_dir}"))
        return
    staged = json.loads(marker.read_text())
    for instance_id in staged.get("instances", []):
        path = store._instance_file(instance_id)
        if path.exists():
            path.unlink()
        d = data_dir / "instances" / instance_id
        if d.is_dir():
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
        if not quiet:
            out(f"  {green('✓')} removed staged instance {instance_id}")
    for slug in staged.get("slices", []):
        status, _ = api.call("DELETE", f"/api/v1/slices/{slug}")
        if not quiet:
            out(f"  {green('✓') if status in (204, 404) else red('✗')} DELETE /api/v1/slices/{slug} {status}")
    marker.unlink()


def act_live(world, api, caps, data_dir: Path, metrics_url):
    from models.slices import Finding
    from services.slices import manifest, stages, store

    act("Live: the Terrain API and the map", f"The simulated stack at {api.url}: every guest reports as running, nothing is reachable over SSH")
    out(f"  core {bold(caps['core']['name'])} ({caps['core']['scope']})   {len(caps['images'])} images   {len(caps['roles'])} roles   "
        f"{len(caps['isps'])} ISPs   jobs: {yellow('no')} {dim('(simulated)')}")
    out()
    clean(api, data_dir, quiet=True)
    world.instances = []
    staged = {"instances": [], "slices": []}
    for name in STAGED:
        raw = read_example(name)
        spec = load_spec(raw)
        status, put = api.call("PUT", f"/api/v1/slices/{spec.slug}", raw)
        api.show("PUT", f"/api/v1/slices/{spec.slug}", status)
        if status >= 300:
            out(red(f"  {put}"))
            return
        if put.get("created"):
            staged["slices"].append(spec.slug)
            (data_dir / MARKER).write_text(json.dumps(staged))
        status, val = api.call("POST", f"/api/v1/slices/{spec.slug}/validate", raw)
        api.show("POST", f"/api/v1/slices/{spec.slug}/validate", status)
        if status != 200 or not val.get("valid"):
            if isinstance(val, dict):
                findings_table([Finding.model_validate(f) for f in val.get("findings", [])])
            else:
                out(red(f"  {val}"))
            return
        alloc, findings = world.resolve(spec)
        if alloc is None:
            out(red(f"  {spec.slug} no longer fits: " + "; ".join(f.message for f in findings if f.severity == 'error')))
            return
        record = world.record(spec, alloc)
        record.stages_done = list(stages.CREATE_STAGES)
        if not store.claim_instance(record):
            out(red(f"  An instance {record.id} already exists in {data_dir}; remove it first (task demo -- --clean)"))
            return
        staged["instances"].append(record.id)
        (data_dir / MARKER).write_text(json.dumps(staged))
        world.instances.append(record)
        out()
        out(f"  {bold(record.id)}  {spec.slug} {dim(put['version'][:23] + '…')}")
        real = {"validate": f"POST /validate: {len(val['findings'])} findings",
                "allocate": f"{len(alloc.static_addresses)} static addresses, {len(alloc.vnets)} vnet{'s' if len(alloc.vnets) != 1 else ''}"
                            + (f", {', '.join(alloc.public_prefixes)}" if alloc.public_prefixes else ""),
                "render": f"Terraform input for {len(alloc.sites) + len(alloc.hosts)} guests (certificates need the core CAs)",
                "ready": "manifest"}
        for stage in stages.CREATE_STAGES:
            if stage in real:
                out(f"     {green('✓')} {stage:<11} {real[stage]}", 0.12)
            else:
                out(f"     {dim('◌')} {dim(f'{stage:<11} simulated: needs Proxmox')}", 0.12)
        with store.update_instance(record.id) as r:
            r.manifest = manifest.build(r, {})
    out()
    pause()

    status, _ = api.call("GET", "/api/v1/instances")
    api.show("GET", "/api/v1/instances", status)
    status, mb = api.call("GET", "/api/v1/instances/mb")
    api.show("GET", "/api/v1/instances/mb", status)
    status, layer = api.call("GET", "/api/v1/map")
    api.show("GET", "/api/v1/map", status)
    status, points = api.call("GET", "/api/v1/attachment-points")
    api.show("GET", "/api/v1/attachment-points", status)
    out()
    if isinstance(mb, dict):
        running = Counter(h.get("status") for h in mb.get("hosts", []))
        pools = Counter(h["labels"].get("ghosts.pool") for h in mb["hosts"] if h.get("labels", {}).get("ghosts.pool"))
        dns = ", ".join(f"{d['name']} → {d['data']}" for d in mb["dns"])
        out(f"  The manifest of {bold('mb')}: {len(mb['sites'])} sites, {len(mb['hosts'])} hosts "
            f"({', '.join(f'{n} {s}' for s, n in running.items())}), DNS {dns}")
        out("  GHOSTS pre-registers machines from it: " + ", ".join(f"{n} in pool {p}" for p, n in pools.items()))
    if isinstance(layer, dict):
        kinds = Counter(f["properties"]["kind"] for f in layer["features"])
        out("  The map layer: " + ", ".join(f"{n} {k}s" for k, n in kinds.items()))
        for f in layer["features"]:
            p = f["properties"]
            if p["kind"] in ("site", "internet-host"):
                lon, lat = f["geometry"]["coordinates"]
                out(f"     {magenta(p['instance'])} {p['kind']:<14} {p['name']:<10} {dim(f'{lat:.2f}, {lon:.2f}')}", 0.04)
    if isinstance(points, list):
        lum = next((p for p in points if p.get("router") == "lum-chi"), None)
        if lum:
            out(f"  The API now offers lum-chi with {bold(lum['free_static_addresses'])} free static addresses")
    out()
    ui = os.environ.get("DEMO_UI_URL", "http://localhost:3000")
    say(f"Open {ui} (reload if it is already open; token dev-token): the three instances are on the map, in "
        "Chicago, New York and Ashburn, and under Instances with their manifests.")
    pause(6)

    if metrics_url:
        out(bold("  Cut lum-chi off"))
        try:
            urllib.request.urlopen(urllib.request.Request(f"{metrics_url}/routers/lum-chi/down", method="POST"), timeout=3)
        except (urllib.error.URLError, OSError) as e:
            out(dim(f"  router-metrics at {metrics_url} didn't answer ({getattr(e, 'reason', e)}); skipping"))
        else:
            out(f"  {dim('POST')} {metrics_url}/routers/lum-chi/down")
            say("Within about ten seconds lum-chi stops reporting and its links go down on the map. Two instances hang "
                "off it: Meridian HQ has one WAN and loses the internet. The routed campus has a second, on are-chi, "
                "and on a deployment its BGP session there keeps 6.0.2.0/24 reachable (docs/slices.md, verification step 2).")
            pause(12)
            urllib.request.urlopen(urllib.request.Request(f"{metrics_url}/routers/lum-chi/up", method="POST"), timeout=3)
            out(f"  {dim('POST')} {metrics_url}/routers/lum-chi/up  {green('restored')}")
    out()
    out(dim("  The staged instances stay until: task demo -- --clean"))


def finale(world):
    out()
    out(bold(magenta("━━ What just happened " + "━" * 66)))
    out(f"  {bold('Fifty lines')} of YAML became an enterprise on the simulated internet:")
    out("    sites, addresses, vnets, NAT, BGP, DNS and certificates.")
    out(f"  {bold('Mistakes')} came back as findings with fixes, before anything was built.")
    out(f"  {bold('Tenants')} shared the core without colliding, and the allocator said where the next one fits.")
    out(f"  {bold('The same code')} runs behind task slice:*, the Terrain API, the web UI and GHOSTS.")
    out()
    out(f"  On a deployment:  {cyan('task slice:up SLICE=examples/slices/meridian-bank.yaml')}")
    out(f"  Read more:        {cyan('docs/slices.md')}   Output of act 4: {cyan(str(OUT))}")
    out()


def main():
    parser = argparse.ArgumentParser(description="A narrated demo of GraySpace slices")
    pace = parser.add_mutually_exclusive_group()
    pace.add_argument("--auto", action="store_true", help="Advance on a timer instead of Enter")
    pace.add_argument("--fast", action="store_true", help="No pauses (CI, a quick check)")
    parser.add_argument("--offline", action="store_true", help="Skip the live act")
    parser.add_argument("--clean", action="store_true", help="Remove what a previous live act staged, and exit")
    parser.add_argument("--api", default=os.environ.get("DEMO_API_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.environ.get("GRAYSPACE_API_TOKEN", "dev-token"))
    parser.add_argument("--metrics", default=os.environ.get("DEMO_METRICS_URL", "http://localhost:9342"),
                        help="The router-metrics simulator ('' to skip cutting lum-chi off)")
    parser.add_argument("--data-dir", type=Path, default=SIM_DATA, help="The simulated API's DATA_PATH")
    args = parser.parse_args()
    MODE["pace"] = "auto" if args.auto else "fast" if args.fast or not sys.stdin.isatty() and not args.auto else "interactive"

    api = Api(args.api, args.token)
    caps, why_not = (None, "--offline") if args.offline and not args.clean else probe(api)
    if args.clean:
        if caps is None:
            print(f"Can't clean: {why_not}")
            return 1
        bootstrap(args.data_dir)
        clean(api, args.data_dir)
        return 0
    bootstrap(args.data_dir if caps else Path(tempfile.mkdtemp(prefix="grayspace-demo-")))

    out()
    out(bold("  G R A Y S P A C E   ·   slices"))
    out(dim("  An enterprise on the simulated internet, from a spec. Nothing below needs Proxmox."))
    out(dim(f"  Core: {tfvars_path().relative_to(ROOT)}   Live act: {'yes, ' + args.api if caps else 'no: ' + why_not}"))
    pause(1.5)

    world = World()
    act_base(world)
    act_spec(world)
    spec, alloc = act_allocate(world)
    act_render(world, spec, alloc)
    act_mistakes(world)
    act_tenants(world)
    if caps:
        act_live(world, api, caps, args.data_dir, args.metrics)
    finale(world)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
