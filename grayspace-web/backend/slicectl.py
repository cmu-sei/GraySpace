#!/usr/bin/env python3
"""Slices from the command line: the same code the API jobs run, so
operators and CI don't need the API.

    slicectl.py validate examples/slices/meridian-bank.yaml [--base grayspace/2026.11 --scope full]
    slicectl.py plan     examples/slices/meridian-bank.yaml
    slicectl.py up       examples/slices/meridian-bank.yaml [--prefix mb2]
    slicectl.py down     examples/slices/meridian-bank.yaml [--prefix mb2]
    slicectl.py status   examples/slices/meridian-bank.yaml

The Taskfile wraps these (task slice:validate SLICE=...). Settings come from the GraySpace
checkout this file is in and its .environment, as Terraform and Ansible see them; jobs take the
node lock like task apply does. validate --base checks a spec against a shipped base version
without any deployment, as CI does.
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _environment_file():
    for name in (".environment", "environment"):
        if (ROOT / name).is_file():
            return ROOT / name
    return None


def bootstrap(tfvars: Path):
    """Backend settings from the checkout and its environment file, before the backend is imported."""
    env_file = _environment_file()
    values = {}
    if env_file:
        out = subprocess.run(["bash", "-c", 'set -a; source "$1" >/dev/null; env -0', "bash", str(env_file)],
                             capture_output=True, check=True).stdout.decode()
        values = dict(item.split("=", 1) for item in out.split("\0") if "=" in item)
    defaults = {
        "ENVIRONMENT_ID": values.get("TF_VAR_environment_id", "offline"),
        "PROMETHEUS_URL": "http://prometheus.invalid:9090",
        "PROXMOX_API_HOST": values.get("TF_VAR_proxmox_host_addr", "proxmox.invalid"),
        "PROXMOX_NODE": values.get("TF_VAR_proxmox_node", "pve"),
        "PROXMOX_BLOCKSTORE": values.get("TF_VAR_proxmox_blockstore", ""),
        "TFVARS_PATH": str(tfvars),
        "BASES_PATH": str(ROOT / "bases"),
        "SCRIPTS_PATH": str(ROOT / "scripts"),
        "CODEBASE_PATH": str(ROOT),
        "SSH_KEY_PATH": str(ROOT / ".ssh" / "terraform_key"),
        "DATA_PATH": values.get("GRAYSPACE_DATA_PATH") or str(ROOT / "terraform" / "grayspace-data"),
    }
    if values.get("PROXMOX_VE_API_TOKEN"):
        defaults["PROXMOX_API_TOKEN"] = values["PROXMOX_VE_API_TOKEN"]
    elif values.get("PROXMOX_VE_USERNAME"):
        defaults["PROXMOX_API_USER"] = values["PROXMOX_VE_USERNAME"]
        defaults["PROXMOX_API_PASSWORD"] = values.get("PROXMOX_VE_PASSWORD", "")
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    os.environ.setdefault("ENVIRONMENT_FILE", str(env_file) if env_file else "")
    os.chdir(HERE)  # config.py reads .env from the working directory; there is none for the CLI
    sys.path.insert(0, str(HERE))
    return bool(env_file)


def load_spec_file(path: Path):
    from pydantic import ValidationError

    from models.slices import SliceSpec

    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    try:
        return SliceSpec.model_validate(data), []
    except ValidationError as e:
        from models.slices import Finding

        return None, [
            Finding(code="INVALID_SPEC", severity="error", path=".".join(str(p) if isinstance(p, str) else f"[{p}]" for p in err["loc"]).replace(".[", "["),
                    message=err["msg"])
            for err in e.errors()
        ]


def print_findings(findings):
    for f in findings:
        mark = "❌" if f.severity == "error" else "⚠️ "
        hint = f"  (suggestion: {json.dumps(f.suggestion)})" if f.suggestion is not None else ""
        print(f"{mark} {f.code} at {f.path or '/'}: {f.message}{hint}")


async def follow(job_id, stop: asyncio.Event):
    from services.slices import store

    path = store.job_log(job_id)
    position = 0
    while True:
        if path.exists():
            with open(path) as f:
                f.seek(position)
                chunk = f.read()
                position = f.tell()
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
        if stop.is_set():
            return
        await asyncio.sleep(0.5)


async def run_job(job):
    from services.slices import runner, stages  # noqa: F401  (registers the job kinds)

    stop = asyncio.Event()
    tail = asyncio.create_task(follow(job.id, stop))
    try:
        result = await runner.execute(job.id)
    finally:
        stop.set()
        await tail
    print(f"\nJob {job.id}: {result.state}" + (f": {result.error}" if result.error else ""))
    return result


async def cmd_validate(args, spec):
    from services.slices import service

    allocation, findings = await service.validate(spec, prefix=args.prefix, check_capacity=args.capacity)
    print_findings(findings)
    if allocation:
        from services.slices import plan

        need = plan.required_capacity(allocation)
        print(f"✅ {spec.slug} {spec.version()[:19]}…: {len(allocation.sites)} sites, {len(allocation.hosts)} hosts; "
              f"needs {need['cores']} cores, {need['memory_mib']} MiB, {need['disk_gib']} GiB")
        for s in allocation.sites:
            wans = ", ".join(f"{w.router} {w.ip}" for w in s.wans)
            print(f"   site {s.name}: {s.edge} on {wans}; " + ", ".join(f"{g.name} {g.cidr}" for g in s.segments))
    return 0 if not service.errors(findings) else 1


def _job(kind, target, stage_list, slice_ref, result=None):
    from services.slices.runner import new_job

    return new_job(kind, target, stage_list, slice_ref=slice_ref, result=result)


async def cmd_plan(args, spec):
    from models.instances import SliceRef
    from services.slices import stages, store

    version, _ = store.put_slice(spec)
    job = _job("plan_slice", spec.slug, stages.PLAN_STAGES, SliceRef(slug=spec.slug, version=version),
               result={"prefix": args.prefix} if args.prefix else None)
    result = await run_job(job)
    if result.result:
        print(json.dumps({k: v for k, v in result.result.items() if k != "allocation"}, indent=2))
    return 0 if result.state == "succeeded" else 1


async def cmd_up(args, spec):
    from datetime import datetime, timezone

    from models.instances import InstanceRecord, SliceRef
    from services.slices import service, stages, store

    version, _ = store.put_slice(spec)
    instance_id = args.prefix or spec.prefix
    record = store.get_instance(instance_id)
    if record and record.live:
        if record.slice.version != version or record.slice.slug != spec.slug:
            print(f"❌ Instance {instance_id} runs {record.slice.slug} {record.slice.version[:19]}…; take it down first (task slice:down)")
            return 1
        if record.state == "ready":
            print(f"✅ Instance {instance_id} is ready")
            return 0
        print(f"Resuming instance {instance_id} ({record.state}) after {', '.join(record.stages_done) or 'no stages'}")
    else:
        _, findings = await service.validate(spec, prefix=instance_id, check_capacity=True)
        print_findings(findings)
        if service.errors(findings):
            return 1
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        record = InstanceRecord(id=instance_id, slice=SliceRef(slug=spec.slug, version=version), state="pending", created=now, updated=now)
        if not store.claim_instance(record):
            print(f"❌ An instance {instance_id} already exists")
            return 1
    job = _job("create_instance", instance_id, stages.CREATE_STAGES, record.slice)
    with store.update_instance(instance_id) as r:
        r.jobs.append(job.id)
    result = await run_job(job)
    return 0 if result.state == "succeeded" else 1


async def cmd_down(args, spec):
    from services.slices import stages, store

    instance_id = args.prefix or spec.prefix
    record = store.get_instance(instance_id)
    if record is None or not record.live:
        print(f"✅ No live instance {instance_id}")
        return 0
    job = _job("destroy_instance", instance_id, stages.DESTROY_STAGES, record.slice)
    with store.update_instance(instance_id) as r:
        r.jobs.append(job.id)
    result = await run_job(job)
    return 0 if result.state == "succeeded" else 1


async def cmd_status(args, spec):
    from services.slices import manifest, stages, store

    instance_id = args.prefix or spec.prefix
    record = store.get_instance(instance_id)
    if record is None:
        print(f"No instance {instance_id}")
        return 1
    built = manifest.build(record, await stages.statuses(instance_id) if record.live else {})
    print(json.dumps({**built.model_dump(mode="json"), "error": record.error, "stages_done": record.stages_done}, indent=2))
    return 0


COMMANDS = {"validate": cmd_validate, "plan": cmd_plan, "up": cmd_up, "down": cmd_down, "status": cmd_status}


def main(argv=None):
    parser = argparse.ArgumentParser(description="GraySpace slices: validate, plan, up, down, status")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("spec", help="A slice spec (YAML or JSON)")
    parser.add_argument("--prefix", help="Instance ID / hostname prefix, to run a second copy (default: the spec's)")
    parser.add_argument("--base", help="validate: against a shipped base version (e.g. grayspace/2026.11) instead of the deployed tfvars")
    parser.add_argument("--scope", default="full", choices=["full", "tier1"], help="validate --base: the scope")
    parser.add_argument("--tfvars", help="The deployed tfvars (default terraform/terraform.tfvars.json)")
    parser.add_argument("--cwd", default=os.getcwd(), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = Path(args.cwd) / spec_path
    tmp = None
    if args.base:
        if args.command != "validate":
            parser.error("--base applies to validate only")
        tmp = tempfile.NamedTemporaryFile(suffix=".tfvars.json", delete=False)
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_tfvars.py"), f"--scope={args.scope}", f"--base={args.base}",
                              f"--output-file={tmp.name}"], capture_output=True, text=True)
        if out.returncode != 0:
            print(out.stdout + out.stderr)
            return 1
        tfvars = Path(tmp.name)
        offline_data = tempfile.mkdtemp(prefix="slicectl-")  # offline: no live instances
        os.environ["DATA_PATH"] = offline_data
    else:
        tfvars = (Path(args.cwd) / args.tfvars).resolve() if args.tfvars else ROOT / "terraform" / "terraform.tfvars.json"
    has_env = bootstrap(tfvars)
    args.capacity = has_env and not args.base
    try:
        spec, problems = load_spec_file(spec_path)
        if spec is None:
            print_findings(problems)
            return 1
        return asyncio.run(COMMANDS[args.command](args, spec))
    finally:
        if tmp:
            os.unlink(tmp.name)
            shutil.rmtree(offline_data, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
