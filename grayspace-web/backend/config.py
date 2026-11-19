"""Configuration management for GraySpace Web Application."""
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Deployment scoping - CRITICAL for multi-developer isolation
    environment_id: str

    # Terraform tfvars path - source of metadata for topology
    tfvars_path: str = "/workspaces/terraform/terraform.tfvars.json"

    # Shipped base versions (bases/<name>/<version>/*.csv) and the directory holding topology.py,
    # the loader that reads them. Ansible copies both next to the backend on deployed hosts.
    bases_path: str = "/workspaces/bases"
    scripts_path: str = "/workspaces/scripts"

    # Prometheus connection
    prometheus_url: str
    # True when prometheus_url was read from the inventory Terraform writes (the container): base
    # jobs then read it again after each deploy
    prometheus_from_inventory: bool = False

    # Proxmox API connection (supports both token and user/password auth)
    proxmox_api_host: str
    proxmox_api_token: Optional[str] = None
    proxmox_api_user: Optional[str] = None
    proxmox_api_password: Optional[str] = None
    proxmox_node: str
    proxmox_verify_ssl: bool = False
    # Block storage guests' disks go on (TF_VAR_proxmox_blockstore), for the storage capacity
    # /capabilities reports. Optional: jobs read it from their environment file.
    proxmox_blockstore: Optional[str] = None

    # SSH connection
    ssh_key_path: str = "/root/.ssh/terraform_key"
    ssh_username: str = "root"

    # Codebase path (parent of terraform/ and ansible/ directories)
    codebase_path: str = "/workspaces"

    # Terrain API auth. The bearer token is required on the
    # contract routes, every write route and console/desktop sessions; unset, they return 503.
    api_token: Optional[str] = Field(None, validation_alias="GRAYSPACE_API_TOKEN")
    # Swagger at /api/docs (routers/api_docs.py): Try it out sends only GETs unless this is true
    api_docs_writes: bool = Field(False, validation_alias="GRAYSPACE_API_DOCS_WRITES")
    # A directory with Swagger UI's files (swagger-ui-dist); unset, /api/docs loads them from a CDN
    swagger_ui_path: Optional[str] = None
    # Browser origins allowed to call the API, comma-separated. Empty allows same-origin only.
    cors_origins: str = Field("", validation_alias="GRAYSPACE_CORS_ORIGINS")

    # Slices, instances, jobs and local images. Defaults to grayspace-data/ next to
    # the tfvars.
    data_path: Optional[str] = None
    # Base presets (services/presets.py), shared by every deployment on one server: the control
    # plane sets <deployments root>/presets. Defaults to presets/ in the data dir.
    presets_path: Optional[str] = None
    # Environment file with the TF_VAR_* and PROXMOX_VE_* settings jobs run Terraform with.
    # Defaults to .environment, then environment, in codebase_path.
    environment_file: Optional[str] = None
    # Address pool for slice segments without an explicit CIDR
    slice_pool: str = "192.168.0.0/16"
    # The management network every container's last NIC is on. Slice address space must
    # not overlap it.
    mgmt_subnet: str = "10.0.0.0/16"
    # Apply attempts before a slice apply counts as not converged, as in task apply
    slice_apply_attempts: int = 6
    # Terraform's -parallelism for what writes to Proxmox (apply, destroy) and for what only reads
    # (plan). Creates contend for the cluster's storage lock (cfs-lock 'storage-<id>'), and too many
    # at once time out; reads are cheap. A Proxmox connection's apply_parallelism and
    # plan_parallelism (Admin > Platform) override these for the deployments placed on it.
    terraform_apply_parallelism: int = 10
    terraform_plan_parallelism: int = 30
    # Seconds a base deploy's check stage waits for every router and BGP session to come up
    base_check_timeout: int = 600
    # Seconds to wait for SSH on every slice host after apply
    slice_ssh_timeout: int = 600
    # The management network's gateway and DNS server, for slice guests whose DHCP lease doesn't
    # show them. Packages are installed over management before the simulated routes return.
    slice_mgmt_gateway: Optional[str] = None
    slice_mgmt_resolver: Optional[str] = None
    # Validity of certificates slices request from base CAs, up to the CA maximum (2160h)
    slice_certificate_validity: str = "720h"

    # How long the deployment's guest list from Proxmox is reused, in seconds (services/proxmox_client.py)
    proxmox_guest_cache_seconds: float = 4.0

    # Local development without Proxmox (services/dev_simulation.py): report every tfvars guest as
    # a running container, keeping start/stop in memory. The Aspire AppHost's simulated mode sets it.
    dev_simulate_deployment: bool = False
    # The stopped hostnames, shared with the router-metrics simulator. Defaults to the data dir.
    dev_simulation_state_file: Optional[str] = None
    # Simulated jobs (services/dev_jobs.py): the tfvars a simulated base deploy writes, and each stage's length
    dev_simulation_tfvars: Optional[str] = None
    dev_simulation_stage_seconds: float = 1.0

    # Application settings
    secret_key: str = "dev-secret-key-change-in-production"
    console_session_timeout: int = 1800  # 30 minutes
    state_poll_interval: int = 15  # seconds

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def data_dir(self) -> Path:
        return Path(self.data_path) if self.data_path else Path(self.tfvars_path).parent / "grayspace-data"

    @property
    def presets_dir(self) -> Path:
        return Path(self.presets_path) if self.presets_path else self.data_dir / "presets"


settings = Settings()
