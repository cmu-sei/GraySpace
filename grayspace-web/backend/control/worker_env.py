"""A worker's process environment: the backend's settings (config.py) for one deployment, derived
from its environment file as container/entrypoint.sh did for the single deployment."""
import os
import re
from pathlib import Path
from typing import Dict, Optional

from control import registry
from control.settings import settings

# The control plane's own environment carries no deployment's settings into a worker.
NOT_INHERITED = re.compile(r"^(TF_VAR_|PROXMOX_|DEV_SIMULAT)|^(ENVIRONMENT_ID|TFVARS_PATH|DATA_PATH|CODEBASE_PATH|BASES_PATH|"
                           r"SCRIPTS_PATH|SSH_KEY_PATH|ENVIRONMENT_FILE|PRESETS_PATH|PROMETHEUS_URL|PROMETHEUS_FROM_INVENTORY|GRAYSPACE_LOCK_FILE|"
                           # A deployment's, from its Proxmox connection; the server's are TERRAFORM_*_PARALLELISM
                           r"GRAYSPACE_APPLY_PARALLELISM|GRAYSPACE_PLAN_PARALLELISM|"
                           # The control plane answers CORS; a worker answering too would double the headers
                           r"GRAYSPACE_CORS_ORIGINS)$")


def prometheus_from_inventory(work: Path) -> Optional[str]:
    """Prometheus's address in the inventory Terraform writes; none until the first apply."""
    try:
        for line in (work / "ansible" / "inventory" / "ungrouped_inventory.ini").read_text().splitlines():
            fields = line.split()
            if fields and fields[0] == "prometheus":
                for field in fields[1:]:
                    if field.startswith("ansible_host="):
                        return f"http://{field.split('=', 1)[1]}:9090"
    except OSError:
        pass
    return None


def build(record: Dict, env: Dict[str, str]) -> Dict[str, str]:
    """env is the deployment's effective environment file (registry.effective_env)."""
    deployment_id = record["id"]
    work = registry.work_dir(record)
    out = {k: v for k, v in os.environ.items() if not NOT_INHERITED.match(k)}
    out.update({
        "ENVIRONMENT_ID": deployment_id,
        "PROXMOX_API_HOST": env.get("TF_VAR_proxmox_host_addr", ""),
        "PROXMOX_NODE": env.get("TF_VAR_proxmox_node", ""),
        "TFVARS_PATH": str(work / "terraform" / "terraform.tfvars.json"),
        "BASES_PATH": str(work / "bases"),
        "SCRIPTS_PATH": str(work / "scripts"),
        "CODEBASE_PATH": str(work),
        "SSH_KEY_PATH": str(work / ".ssh" / "terraform_key"),
        "DATA_PATH": str(work / "terraform" / "grayspace-data"),
        # Base presets are the server's, shared by the deployments on each base version
        "PRESETS_PATH": str(registry.root() / "presets"),
        "GRAYSPACE_API_TOKEN": settings.api_token or "",
        "PYTHONUNBUFFERED": "1",
    })
    path = registry.environment_file(record)
    if path is not None:
        out["ENVIRONMENT_FILE"] = str(path)
    if env.get("GRAYSPACE_LOCK_FILE"):
        out["GRAYSPACE_LOCK_FILE"] = env["GRAYSPACE_LOCK_FILE"]
    else:
        # An adopted tree without the setting: the API's jobs still take the cluster's lock
        out["GRAYSPACE_LOCK_FILE"] = str(registry.lock_file(env.get("TF_VAR_proxmox_host_addr")))
    if env.get("PROXMOX_VE_API_TOKEN"):
        out["PROXMOX_API_TOKEN"] = env["PROXMOX_VE_API_TOKEN"]
    else:
        out["PROXMOX_API_USER"] = env.get("PROXMOX_VE_USERNAME", "")
        out["PROXMOX_API_PASSWORD"] = env.get("PROXMOX_VE_PASSWORD", "")
    for name in ("TF_VAR_proxmox_blockstore",):
        if env.get(name):
            out["PROXMOX_BLOCKSTORE"] = env[name]
    # Set in the environment file (or, for the adopted tree, the container's), else from the inventory
    fixed = env.get("PROMETHEUS_URL") or (os.environ.get("PROMETHEUS_URL") if record["kind"] == "external" else None)
    if fixed:
        out["PROMETHEUS_URL"] = fixed
    else:
        out["PROMETHEUS_URL"] = prometheus_from_inventory(work) or "http://prometheus.invalid:9090"
        # Base jobs read it again from the inventory after each deploy
        out["PROMETHEUS_FROM_INVENTORY"] = "true"
    if env.get("TF_VAR_proxy"):
        out.update(http_proxy=env["TF_VAR_proxy"], https_proxy=env["TF_VAR_proxy"], no_proxy=env.get("TF_VAR_no_proxy", ""))
    if settings.simulate:
        out.update(simulated(record))
    return out


def simulated(record: Dict) -> Dict[str, str]:
    """Development: every tfvars guest reports as running (services/dev_simulation.py). Each
    deployment has its own copy of the tfvars, so its runtime changes are its own."""
    deployment_id = record["id"]
    base = registry.directory(deployment_id) / "sim"
    base.mkdir(exist_ok=True)
    tfvars = base / "terraform.tfvars.json"
    source = Path(settings.simulate_tfvars) if settings.simulate_tfvars else Path(settings.source_path) / "tests" / "golden" / "tfvars-full.json"
    # The bootstrap deployments start deployed; one made later starts empty, for its base deploy to fill
    first_start = not (base / "started").exists()
    if first_start and deployment_id in settings.bootstrap_ids and not tfvars.exists() and source.is_file():
        tfvars.write_bytes(source.read_bytes())
    (base / "started").touch()
    state = base / "dev-simulation-stopped.json"
    if settings.simulate_state_file and settings.bootstrap_ids[:1] == [deployment_id]:
        state = Path(settings.simulate_state_file)
    # The simulated jobs' pace and failures (services/dev_jobs.py) are the control plane's to set
    passed = {k: os.environ[k] for k in ("DEV_SIMULATION_STAGE_SECONDS", "DEV_SIMULATION_FAIL") if os.environ.get(k)}
    return {
        **passed,
        "DEV_SIMULATE_DEPLOYMENT": "true",
        "DEV_SIMULATION_STATE_FILE": str(state),
        "DEV_SIMULATION_TFVARS": str(source),
        "TFVARS_PATH": str(tfvars),
        "DATA_PATH": str(base / "grayspace-data"),
        "CODEBASE_PATH": str(settings.source_path),
        "BASES_PATH": str(Path(settings.source_path) / "bases"),
        "SCRIPTS_PATH": str(Path(settings.source_path) / "scripts"),
        "PROXMOX_API_HOST": "proxmox.simulated",
        "PROXMOX_NODE": "pve",
        "PROXMOX_API_TOKEN": "dev@pve!simulated=00000000-0000-0000-0000-000000000000",
        "PROXMOX_BLOCKSTORE": "local-lvm",
        "PROMETHEUS_URL": os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
        "PROMETHEUS_FROM_INVENTORY": "false",
    }
