"""What the control plane's deployments list shows of this deployment (control/api.py): the base,
enclave instances and jobs in one call, and the node's capacity when asked. Not in the contract."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from services.auth import require_token
from services.base_service import get_deployed_base
from services.slices import store

router = APIRouter(prefix="/api/v1/summary", tags=["summary"], dependencies=[Depends(require_token)])

BASE_KINDS = {"deploy_base", "configure_base", "destroy_base"}


def _brief(job) -> Optional[Dict[str, Any]]:
    if job is None:
        return None
    return {"id": job.id, "kind": job.kind, "state": job.state, "stage": job.stage, "attempt": job.attempt, "finished": job.finished}


def _draft() -> Dict[str, Any]:
    from services import rollout

    try:
        found = rollout.plan()
    except Exception:  # a draft naming what no longer exists still lists the deployment
        return {"saved": (store.data_dir() / "draft.json").exists(), "changes": None}
    return {"saved": (store.data_dir() / "draft.json").exists(), "changes": found.changes}


@router.get("")
async def get_summary(capacity: bool = Query(False, description="Also the Proxmox node's capacity (asks Proxmox)")):
    base = await get_deployed_base()
    instances = store.list_instances()
    jobs = store.list_jobs()
    active = [j for j in jobs if j.state in ("queued", "running") and j.kind != "plan_slice"]
    base_jobs = [j for j in jobs if j.kind in BASE_KINDS]
    out = {
        "base": {"status": base.status, "version": base.version.model_dump() if base.version else None,
                 "scope": base.version.scope if base.version else None, "routers": base.routers.expected if base.version else None},
        "instances_live": sum(1 for i in instances if i.live and i.state != "failed"),
        "instances_failed": sum(1 for i in instances if i.state == "failed"),
        "active_job": _brief(active[-1] if active else None),
        "last_base_job": _brief(base_jobs[-1] if base_jobs else None),
        "updated": max((j.finished or j.started or j.created for j in jobs), default=None),
        "draft": _draft(),
    }
    if capacity:
        from services.capabilities import capacity as node_capacity

        found = await node_capacity()
        out["capacity"] = found.model_dump() if found else None
    return out
