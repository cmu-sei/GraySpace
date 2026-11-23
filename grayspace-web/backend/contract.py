"""The Terrain API contract: the /api/v1 routes GHOSTS builds against.

The full app (main.py) serves these alongside routes private to the web UI. The contract spec,
openapi/grayspace-v1.json at the repository root, is generated from these routers alone, so a
change to a UI route never changes the contract. Next to it:

- openapi/grayspace-enclave-v1.json: JSON Schema of the enclave spec, and the same schema as
  openapi/grayspace-slice-v1.json, its name before contract 1.5 (deprecated)
- openapi/grayspace-events-v1.json: JSON Schema of /api/v1/events messages, which OpenAPI can't
  describe because it is a websocket
- openapi/grayspace-deployments-events-v1.json: the same for /api/v1/deployments/events

Since 1.6 one GraySpace runs several deployments (docs/deployments.md). Every route but the
control plane's is a deployment's, under its base URL /deployments/{deployment}; the spec says so
with servers. The control plane serves /api/v1/deployments (control/api.py) and, since 1.7,
sign-in, users, the audit log and nodes (control/accounts.py); since 1.9, the platform's Proxmox and
Crucible connections (control/platform_api.py).

Regenerate them after changing a contract route or model:

    cd grayspace-web/backend && python contract.py
"""
import json
import os
from pathlib import Path

# Importing the routers loads settings. The spec doesn't depend on their values, so supply
# placeholders for the required ones; a real .env or environment still takes precedence.
for _key in ("ENVIRONMENT_ID", "PROMETHEUS_URL", "PROXMOX_API_HOST", "PROXMOX_NODE"):
    os.environ.setdefault(_key, "unused")

from fastapi import FastAPI  # noqa: E402
from pydantic import TypeAdapter  # noqa: E402

from control import accounts, platform_api  # noqa: E402
from control import api as deployments  # noqa: E402
from control.models import DeploymentEvent  # noqa: E402
from models.events import Event  # noqa: E402
from models.slices import SliceSpec  # noqa: E402
from routers import base, ca_bundle, capabilities, images, instances, jobs, player, rollout, slices  # noqa: E402
from routers import map as map_router  # noqa: E402

OPENAPI = Path(__file__).resolve().parents[2] / "openapi"
SPEC = OPENAPI / "grayspace-v1.json"
ENCLAVE_SCHEMA = OPENAPI / "grayspace-enclave-v1.json"
# The same schema under its 1.4 name, for clients that pinned it; removed in 2.0
SLICE_SCHEMA = OPENAPI / "grayspace-slice-v1.json"
EVENTS_SCHEMA = OPENAPI / "grayspace-events-v1.json"
DEPLOYMENT_EVENTS_SCHEMA = OPENAPI / "grayspace-deployments-events-v1.json"

ROUTERS = [base.router, base.legacy_router, rollout.router, player.router, capabilities.router, slices.router, slices.legacy_router, instances.router, jobs.router, images.router,
           map_router.router, ca_bundle.router, deployments.router, accounts.auth_router, accounts.me_router, accounts.users_router,
           accounts.admin_router, platform_api.router]
# The control plane's own routes, at the server's root rather than under a deployment
ROOT_PREFIXES = ("/api/v1/deployments", "/api/v1/auth", "/api/v1/me", "/api/v1/users", "/api/v1/audit", "/api/v1/nodes", "/api/v1/directory",
                 "/api/v1/platform")
SERVERS = [
    {"url": "/deployments/{deployment}", "description": "A deployment (GET /api/v1/deployments lists them)",
     "variables": {"deployment": {"default": "abc", "description": "The deployment's ID (its environment_id)"}}},
    {"url": "/", "description": "The default deployment. Deprecated: name the deployment"},
]


DESCRIPTION = """What the simulated internet offers and how it is doing, and the enclaves built at its edge: the
deployed core version, its inventory (routers, links and core services with their addresses and status)
and DNS (every zone and record), shipped versions and their snapshots, capabilities, attachment points, enclaves,
instances and their manifests, jobs, images and roles, the CA bundle and the map layer. The core itself is deployed,
configured and destroyed through the API too.

One GraySpace runs several deployments, each a core and the enclaves on it. `GET /api/v1/deployments` lists them;
every other route is a deployment's, under its base URL `/deployments/{deployment}` (so the core is
`/deployments/abc/api/v1/core`). Without the prefix, a route reaches the default deployment: that alias is
deprecated. A client such as GHOSTS takes the deployment's base URL as its GraySpace address.

Every route needs a bearer token, sent as `Authorization: Bearer <token>`: the server's service token
(`GRAYSPACE_API_TOKEN`), which may do everything, or a person's session token from `POST /api/v1/auth/login`.
A person sees only the deployments they own or are a member of (others are 404), and may only do what their
access there allows (403 otherwise): `GET /api/v1/me` gives their role, and each deployment's `my_access`
what they may do in it.
Live updates come over the deployment's `/api/v1/events` websocket (`grayspace-events-v1.json`); deployments
being created, changed and removed over `/api/v1/deployments/events` (`grayspace-deployments-events-v1.json`).

**From nothing deployed to a core and a slice** (paths relative to the deployment's base URL):

0. `POST /api/v1/deployments` with `{"id": "abd"}` (at the server's root), unless the deployment exists. It runs on the
   default Proxmox connection; `placement` chooses another, or another node (`GET /api/v1/platform`).
1. `GET /api/v1/core/preflight?version=grayspace/2026.11&scope=tier1`: is the site ready? Fix every `error`.
2. `POST /api/v1/core/deploy` with `{"version": "grayspace/2026.11", "scope": "tier1"}`: returns a job.
3. `GET /api/v1/jobs/{id}` (or its streamed `/log`, or `/events`) until it succeeds; then `GET /api/v1/core` is `healthy`.
4. `PUT /api/v1/enclaves/{slug}` with an enclave spec (`grayspace-enclave-v1.json`); fix any error findings.
5. `POST /api/v1/instances` with `{"enclave": "<slug>"}`: returns a job; when it succeeds, `GET /api/v1/instances/{id}` is the manifest.

Or in one job: `PUT /api/v1/draft` with the core and the enclave instances the deployment should have
(`{"core": {"version": "grayspace/2026.11", "scope": "tier1"}, "enclaves": [{"slug": "<slug>", "prefix": "bk"}]}`),
`GET /api/v1/deploy/plan` to see what would change, then `POST /api/v1/deploy`: a rollout job that destroys what the
draft no longer has, deploys or updates the core, and creates the instances, each as a child job (`GET /api/v1/jobs?parent={id}`).
Change the draft and deploy again to change the deployment. Since 1.8 the draft's core may name a
preset (`preset`), a saved set of runtime changes the rollout applies once the core is built. Since 1.10
the draft's `player` is the deployment's Crucible Player view: its applications, teams, and which core and enclave VMs
each team sees (`GET /api/v1/player/guests`); the rollout applies it last, in its `player` stage.
Since 1.11 a Proxmox connection may set `apply_parallelism` and `plan_parallelism`, Terraform's parallelism for the
deployments placed on it (applies and destroys: how many guests are created at once; plans).
Since 1.12 a deployment's `options.use_custom_image` is deprecated and ignored: every deployment's Alpine guests use
the GraySpace image.
Since 1.13 the core has no `grafana` guest (the web app has the dashboards): `GET /api/v1/player/guests` no longer
lists it, and a draft's Player guest that names it is dropped (since 1.17 without a warning, when the draft is read or saved).
Since 1.14 `GET /api/v1/player` has the view's `url` in Player's UI.
Since 1.15 the draft's `player.assign` gives every guest of the core or of an enclave instance to teams; `player.guests`
lists the guests whose teams differ.
Since 1.16 `GET /api/v1/core/preview` (then `/api/v1/base/preview`) is a shipped version's map before it is deployed, and a Crucible connection may
set `player_url`, Player's UI when it isn't the Player API URL without /api.
Since 1.18 the core has no clients: a core version is ISPs, routers, links, DNS servers and CAs, and hosts on the
simulated internet are enclaves (`grayspace/2026.11`; `grayspace/2026.10` is retired). The inventory's `clients`, a
version summary's `clients` and the capabilities' `host_features` are deprecated and always empty or 0, `guest.update`
no longer comes for a `client`, and `client.move` is no longer a core action (a preset that has one lists it in its
`problems`), and a draft's Player guest that names one is dropped without a warning. An image with the `desktop`
feature (`grayspace-desktop`) is a desktop in the browser.
Since 1.19 enclaves have no presets: an enclave is what its spec builds, and a variant is another enclave. The routes
`/api/v1/enclaves/{slug}/presets` and `/api/v1/instances/{id}/presets` are gone, a draft's `enclaves[].preset` is
deprecated and ignored, an instance and a planned instance no longer have `preset` and `active_preset`, and the plan has
no `preset:<id>` stages. An instance's actions are live changes its reset undoes. Core presets are unchanged.
Since 1.20 `POST /api/v1/enclaves/{slug}/validate` takes `instance`, a draft's prefix, to check the spec as that
instance: when it is live and built from the enclave, its prefix, DNS names and links are its own, not conflicts.
Since 1.21 a role can name the image types it runs on (`image_types`, e.g. `[vm]` for `ad-dc`), and a host whose image
is another type gets `INCOMPATIBLE_ROLE`.
Since 1.22 a rollout resumes only while the draft is the one it deployed: after the draft changes, retrying it is refused
(409; deploy instead), and `GET /api/v1/deploy/plan` has `resumable_rollout`, the last rollout when it can still be
retried. A destroyed instance is no longer managed by the draft, however it was destroyed.
Since 1.23 a Proxmox connection chooses the proxy its deployments' guests install packages through (`proxy_mode`: `site`,
the site file's, by default; `none`; or `custom`, with `proxy` and `no_proxy`).
Since 1.24 the hosts of a site with a domain controller (an `ad-dc` host) use it as their resolver (an allocated host's
`resolver`); the DC keeps the core resolver, its DNS's forwarder. A site's `resolver` is still the core's.
Since 1.25 the core has no GHOSTS VM: enclave hosts run GHOSTS clients (the `ghosts-client` role), which report to a
GHOSTS server outside the simulated internet. A deployment's `options.deploy_ghosts` is deprecated and ignored,
`GET /api/v1/player/guests` no longer lists `ghosts`, a draft's Player guest that names it is dropped without a warning,
and the core preflight has no `ghosts-vm` check.
Since 1.26 the base is called the core. Its routes are at `/api/v1/core` (`/api/v1/core/presets` and `/actions` too), and
`/api/v1/base` is their deprecated alias. Responses name it `core` as well as `base`: a draft's and a plan's `core`,
a deployment's `core` and its summary's `last_core_job`, a manifest's `core`, capabilities' `core`, `core_jobs` and
`core_jobs_unavailable_reason`, and the Player guests' `core`. A draft takes `core` or `base` (both, only if they are the same),
and enclave validation and presets `?core=` or `?base=`. The `base` names are deprecated and go in 2.0. Values keep their
names until then: the `base` rollout stage, job kinds such as `deploy_base`, a job's `base` target, the `base` Player
scope and DNS origin, and the `base` enclave.

Tear down in reverse: `DELETE /api/v1/instances/{id}`, then `DELETE /api/v1/core`. Long-running work is always a
job: poll it, read its log, cancel it, or retry it from the stage that failed."""

TAGS = [
    {"name": "deployments", "description": "The deployments this GraySpace runs, each a core and its enclaves under its own "
     "base URL. Served at the server's root"},
    {"name": "auth", "description": "Sign-in with a local account, your sessions and password, and who you are. At the server's root"},
    {"name": "users", "description": "Local accounts: create, change role, disable, reset a password (admins). At the server's root"},
    {"name": "admin", "description": "The audit log and the Proxmox nodes (admins). At the server's root"},
    {"name": "platform", "description": "The Proxmox and Crucible connections deployments run on, defined once by admins. At the server's root"},
    {"name": "core", "description": "The deployed core: version, health, inventory, DNS and shipped versions; "
     "and its lifecycle as jobs: preflight, deploy, configure, destroy"},
    {"name": "rollout", "description": "The draft (the core and enclave instances a deployment should have) and the rollout job "
     "that makes the deployment match it"},
    {"name": "player", "description": "The deployment's Crucible Player view: what the draft's player section can use, and what is applied"},
    {"name": "capabilities", "description": "What a slice can use on a core version, and where it can attach"},
    {"name": "enclaves", "description": "Stored enclave specs, versioned by content: store, validate, plan"},
    {"name": "instances", "description": "Enclaves built on the core: create, inspect (the manifest), act on at runtime, destroy"},
    {"name": "jobs", "description": "Every deploy, create, destroy and plan: progress, streamed log, cancel, retry"},
    {"name": "images", "description": "The image catalog and the roles slice hosts can take"},
    {"name": "map", "description": "The map layer as GeoJSON, for any map renderer"},
    {"name": "ca", "description": "The core CAs' root certificates"},
    {"name": "base (deprecated)", "description": "The core routes under their name before 1.26, when the core was the base. Removed in 2.0"},
    {"name": "slices (deprecated)", "description": "The enclave routes under their 1.4 name, for existing clients. Removed in 2.0"},
]


def contract_app() -> FastAPI:
    app = FastAPI(
        title="GraySpace Terrain API",
        description=DESCRIPTION,
        version="1.26.0",
        openapi_tags=TAGS,
        servers=SERVERS,
    )
    for router in ROUTERS:
        app.include_router(router)
    openapi = app.openapi

    def with_root_servers():
        spec = openapi()
        for path, item in spec["paths"].items():
            if path.startswith(ROOT_PREFIXES):
                item["servers"] = [{"url": "/", "description": "The GraySpace server"}]
        return spec

    app.openapi = with_root_servers
    return app


def spec_text() -> str:
    return json.dumps(contract_app().openapi(), indent=2) + "\n"


def _spec_schema(name: str, title: str) -> str:
    schema = SliceSpec.model_json_schema()
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"https://grayspace/schemas/{name}", "title": title, **schema}
    return json.dumps(schema, indent=2) + "\n"


def enclave_schema_text() -> str:
    return _spec_schema("grayspace-enclave-v1.json", "GraySpace enclave spec (apiVersion grayspace/v1, kind Enclave)")


def slice_schema_text() -> str:
    return _spec_schema("grayspace-slice-v1.json", "GraySpace slice spec (apiVersion grayspace/v1, kind Slice). Deprecated: grayspace-enclave-v1.json")


def events_schema_text() -> str:
    schema = TypeAdapter(Event).json_schema(by_alias=True)
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://grayspace/schemas/grayspace-events-v1.json",
              "title": "GraySpace /api/v1/events messages", **schema}
    return json.dumps(schema, indent=2) + "\n"


def deployment_events_schema_text() -> str:
    schema = DeploymentEvent.model_json_schema()
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://grayspace/schemas/grayspace-deployments-events-v1.json",
              "title": "GraySpace /api/v1/deployments/events messages", **schema}
    return json.dumps(schema, indent=2) + "\n"


OUTPUTS = {SPEC: spec_text, ENCLAVE_SCHEMA: enclave_schema_text, SLICE_SCHEMA: slice_schema_text, EVENTS_SCHEMA: events_schema_text,
           DEPLOYMENT_EVENTS_SCHEMA: deployment_events_schema_text}

if __name__ == "__main__":
    OPENAPI.mkdir(exist_ok=True)
    for path, text in OUTPUTS.items():
        path.write_text(text())
        print(f"Wrote {path}")
