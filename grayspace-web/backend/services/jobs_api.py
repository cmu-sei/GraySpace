"""Helpers the job-creating routes share."""
from fastapi import HTTPException

from services.slices import environment
from services.slices.runner import runner


def require_base_jobs():
    """Base job routes need a runner: a GraySpace checkout with Terraform and Ansible."""
    if not runner.running:
        available, why = environment.base_jobs_available()
        raise HTTPException(status_code=503, detail=f"This GraySpace can't run jobs: {why or 'the job runner is not running'}")


def require_jobs():
    """Slice job routes also need a deployed base. Checked on every request."""
    require_base_jobs()
    available, why = environment.jobs_available()
    if not available:
        raise HTTPException(status_code=503, detail=f"This GraySpace can't run slice jobs: {why}")
