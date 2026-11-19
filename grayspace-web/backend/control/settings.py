"""The control plane's settings, from the environment (container/entrypoint.sh, the Aspire AppHost)."""
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# grayspace-web/backend/control/settings.py -> the GraySpace tree
CHECKOUT = Path(__file__).resolve().parents[3]


class SwitcherApp(BaseModel):
    """An app in the header's app switcher. The config spells the keys Id, Name, Url (or id, name, url)."""
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(validation_alias=AliasChoices("Id", "id"))
    name: str = Field(validation_alias=AliasChoices("Name", "name"))
    # Without one, the switcher leaves the app out
    url: Optional[str] = Field(None, validation_alias=AliasChoices("Url", "url"))


# The apps, in the switcher's order; GRAYSPACE_APPS (a JSON array of the same) replaces them
DEFAULT_APPS = [
    {"Id": "scenario-builder", "Name": "Scenario Builder", "Url": "http://localhost:4310"},
    {"Id": "grayspace", "Name": "GraySpace"},
    {"Id": "chronicle", "Name": "Chronicle", "Url": "http://localhost:4320"},
    {"Id": "ghosts", "Name": "GHOSTS"},
]


class ControlSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # The tree new deployments' work trees are copied from (the image's code in the container)
    source_path: str = Field(str(CHECKOUT), validation_alias="GRAYSPACE_SOURCE_PATH")
    # Where deployments live: <id>/deployment.json, environment, work/, run/; locks/
    deployments_path: Optional[str] = Field(None, validation_alias="GRAYSPACE_DEPLOYMENTS_PATH")
    # The site's environment file: Proxmox credentials and defaults every deployment's file sources
    site_environment: Optional[str] = Field(None, validation_alias="GRAYSPACE_SITE_ENVIRONMENT")
    # An existing single-deployment tree to adopt as the default deployment on first start
    adopt_path: Optional[str] = Field(None, validation_alias="GRAYSPACE_ADOPT_PATH")
    # The Python environment Taskfile's tasks use (linked as .venv into new work trees)
    ops_venv: Optional[str] = Field(None, validation_alias="GRAYSPACE_OPS_VENV")

    # The server's service credential: scripts, task, GHOSTS, and the proxy's calls to workers
    api_token: Optional[str] = Field(None, validation_alias="GRAYSPACE_API_TOKEN")
    # Sign-in (control/auth.py): which sources are on, the first admin, how long a session lasts unused
    auth_providers: str = Field("local", validation_alias="GRAYSPACE_AUTH_PROVIDERS")
    bootstrap_admin: Optional[str] = Field(None, validation_alias="GRAYSPACE_BOOTSTRAP_ADMIN")
    session_ttl: int = Field(12 * 3600, validation_alias="GRAYSPACE_SESSION_TTL")
    # Development: <username>:<role>,... created on startup if missing, all with seed_password
    seed_users: str = Field("", validation_alias="GRAYSPACE_SEED_USERS")
    seed_password: Optional[str] = Field(None, validation_alias="GRAYSPACE_SEED_PASSWORD")
    cors_origins: str = Field("", validation_alias="GRAYSPACE_CORS_ORIGINS")
    worker_python: str = Field(sys.executable, validation_alias="BACKEND_PYTHON")
    worker_start_timeout: float = Field(90.0, validation_alias="GRAYSPACE_WORKER_START_TIMEOUT")

    # Development: workers simulate their deployment (services/dev_simulation.py) on this tfvars
    simulate: bool = Field(False, validation_alias="GRAYSPACE_SIMULATE")
    simulate_tfvars: Optional[str] = Field(None, validation_alias="GRAYSPACE_SIMULATE_TFVARS")
    # The first bootstrap deployment's stopped-guests file, which aspire/simulator/router_metrics.py reads
    simulate_state_file: Optional[str] = Field(None, validation_alias="GRAYSPACE_SIMULATE_STATE_FILE")
    # Comma-separated deployment IDs to create on startup if missing (development)
    bootstrap: str = Field("", validation_alias="GRAYSPACE_BOOTSTRAP_DEPLOYMENTS")
    # <id>:<port>: run that deployment's worker under debugpy, listening on the port (development)
    worker_debug: Optional[str] = Field(None, validation_alias="GRAYSPACE_WORKER_DEBUG")

    # The header's app switcher (GET /api/apps): the apps, this one included
    apps: List[SwitcherApp] = Field(default_factory=lambda: [SwitcherApp.model_validate(a) for a in DEFAULT_APPS],
                                  validation_alias="GRAYSPACE_APPS")

    @property
    def root(self) -> Path:
        return Path(self.deployments_path) if self.deployments_path else Path(self.source_path) / "deployments"

    @property
    def site_environment_file(self) -> Optional[Path]:
        if self.site_environment:
            path = Path(self.site_environment)
            return path if path.is_file() else None
        for name in (".environment", "environment"):
            path = Path(self.source_path) / name
            if path.is_file():
                return path
        return None

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def bootstrap_ids(self) -> List[str]:
        return [i.strip() for i in self.bootstrap.split(",") if i.strip()]

    @property
    def provider_list(self) -> List[str]:
        return [p.strip() for p in self.auth_providers.split(",") if p.strip()]

    @property
    def seed_user_list(self) -> List[Tuple[str, str]]:
        pairs = [u.strip().partition(":") for u in self.seed_users.split(",") if u.strip()]
        return [(name, role or "user") for name, _, role in pairs]


settings = ControlSettings()
