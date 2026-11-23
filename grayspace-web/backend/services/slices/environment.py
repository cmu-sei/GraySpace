"""Where jobs run: a GraySpace checkout with Terraform, Ansible and an environment file.

Jobs run only where a checkout, its Terraform state and its environment file live: the GraySpace
container (each deployment's work tree) or a devcontainer (settings.codebase_path). Without one,
/capabilities reports jobs: false and job routes return 503.

Base jobs (deploy, configure, destroy) need the checkout, the tools
and the environment file. Slice jobs also need a deployed base. Both are checked when asked, not
once at startup, so slice jobs become available as soon as a base deploy has created the base's
SSH key.
"""
import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

from config import settings

logger = logging.getLogger(__name__)


def codebase() -> Path:
    return Path(settings.codebase_path)


def base_module() -> Path:
    return codebase() / "terraform"


def slice_module() -> Path:
    return codebase() / "terraform" / "slice"


def images_module() -> Path:
    return codebase() / "terraform" / "images"


def lock_file() -> Path:
    """The node lock task apply, destroy and configure take too (scripts/with-node-lock.sh)."""
    return Path(os.environ.get("GRAYSPACE_LOCK_FILE") or codebase() / "terraform" / ".grayspace.lock")


def inventory_dir() -> Path:
    """Slice inventories live outside ansible/inventory/, which base runs load whole."""
    return codebase() / "ansible" / "slice-inventory"


def environment_file() -> Optional[Path]:
    if settings.environment_file:
        return Path(settings.environment_file)
    for name in (".environment", "environment"):
        if (codebase() / name).is_file():
            return codebase() / name
    return None


def base_jobs_available() -> Tuple[bool, Optional[str]]:
    """Whether base jobs can run here: (available, why not)."""
    if settings.dev_simulate_deployment:
        return True, None  # simulated jobs (services/dev_jobs.py)
    if not (base_module() / "main.tf").is_file() or not (codebase() / "ansible" / "main.yml").is_file():
        return False, f"No GraySpace checkout at {codebase()} (CODEBASE_PATH)"
    for tool in ("terraform", "ansible-playbook"):
        if not shutil.which(tool):
            return False, f"{tool} is not installed"
    if environment_file() is None:
        return False, f"No environment file (.environment or environment) in {codebase()}"
    return True, None


def jobs_available() -> Tuple[bool, Optional[str]]:
    """Whether slice jobs can run here: base jobs can, and a base is deployed. (available, why not)"""
    available, why = base_jobs_available()
    if not available:
        return available, why
    if settings.dev_simulate_deployment:
        from services.topology_service import topology_service

        return (True, None) if topology_service.tfvars_data else (False, "Nothing is deployed; deploy the core first")
    if not (slice_module() / "main.tf").is_file() or not (codebase() / "ansible" / "slice.yml").is_file():
        return False, f"No GraySpace checkout at {codebase()} (CODEBASE_PATH)"
    if not (codebase() / ".ssh" / "terraform_key.pub").is_file():
        return False, "The core's SSH key (.ssh/terraform_key) is missing; deploy the core first (POST /api/v1/core/deploy)"
    return True, None


@lru_cache(maxsize=4)
def _load_env(path: str, mtime: float) -> Dict[str, str]:
    out = subprocess.run(
        ["bash", "-c", 'set -a; source "$1" >/dev/null; env -0', "bash", path],
        capture_output=True, check=True, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/root")},
    ).stdout.decode()
    return dict(item.split("=", 1) for item in out.split("\0") if "=" in item)


def job_env() -> Dict[str, str]:
    """The process environment plus the environment file: what Terraform and Ansible run with."""
    env = dict(os.environ)
    path = environment_file()
    if path is not None:
        env.update(_load_env(str(path), path.stat().st_mtime))
    env.setdefault("TF_IN_AUTOMATION", "1")
    env["ANSIBLE_FORCE_COLOR"] = "0"
    env["ANSIBLE_NOCOLOR"] = "1"
    return env


PARALLELISM_VARS = {"apply": "GRAYSPACE_APPLY_PARALLELISM", "plan": "GRAYSPACE_PLAN_PARALLELISM"}


def parallelism(kind: str) -> int:
    """Terraform's -parallelism for apply and destroy ("apply") or plan ("plan"): the deployment's
    Proxmox connection's, which its environment file exports (control/platform.py), else the server's
    (terraform_apply_parallelism, terraform_plan_parallelism). task apply reads the same variables."""
    default = settings.terraform_apply_parallelism if kind == "apply" else settings.terraform_plan_parallelism
    raw = job_env().get(PARALLELISM_VARS[kind], "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    if raw:
        logger.warning("%s=%r isn't a positive whole number; using %d", PARALLELISM_VARS[kind], raw, default)
    return default


def crucible_team_id() -> Optional[str]:
    """The base module's Crucible Player view team (modules/crucible, from before terraform/player/),
    from the base state's "crucible" output; None when that module is off (every managed deployment:
    their view is services/player.py's) or nothing is deployed. Read from the state file: no Terraform
    run, no Crucible call."""
    import json

    if job_env().get("TF_VAR_configure_crucible", "").lower() not in ("1", "true", "yes"):
        return None
    try:
        value = json.loads((base_module() / "terraform.tfstate").read_text()).get("outputs", {}).get("crucible", {}).get("value")
    except (OSError, ValueError):
        return None
    return value.get("team_id") if isinstance(value, dict) else None


def tf_var(name: str) -> Optional[str]:
    """A TF_VAR_ setting from the environment file, e.g. tf_var('proxmox_blockstore')."""
    return job_env().get(f"TF_VAR_{name}")
