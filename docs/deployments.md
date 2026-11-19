# Deployments

One GraySpace server runs several deployments at once. A deployment is a base (the simulated
internet) and the enclaves built on it, deployed to a Proxmox node with its own `environment_id`.
Deployments can share a Proxmox node or cluster, or use different ones.

## Addressing a deployment

Each deployment has a base URL, `/deployments/{id}`, and its whole API sits under that URL
unchanged: the base is `/deployments/abc/api/v1/base`, its events are
`/deployments/abc/api/v1/events`, a console is `/deployments/abc/ws/console/{hostname}`. The
[Terrain API](api.md) contract is the same for every deployment. A client such as GHOSTS takes the
deployment's base URL as GraySpace's address:

```bash
TerrainProvider__BaseUrl=https://grayspace.example/deployments/abc   # TERRAIN_BASE_URL under Docker Compose
```

The routes without the prefix (`/api/v1/...`, `/ws/...`) reach the **default deployment**. That
alias is deprecated: its responses carry `Deprecation: true` and `X-GraySpace-Deployment: <id>`.
It keeps the web UI, existing GHOSTS configurations and scripts working while they move to
prefixed URLs.

## Sign-in

Every request through the server needs a bearer token (`Authorization: Bearer <token>`, or
`?token=` on a websocket): unauthenticated requests get 401, whatever the route. There are two
kinds:

- **A session**, for a person. `POST /api/v1/auth/login` with `{"username", "password"}` of a local
  account returns a token. It lasts `GRAYSPACE_SESSION_TTL` seconds unused (12 hours by default)
  and ends with `POST /api/v1/auth/logout`. The web UI signs in this way.
- **The service token**, `GRAYSPACE_API_TOKEN`: scripts, `task`, GHOSTS and Scenario Builder. It
  may do everything, as an admin. The server also uses it to call each deployment's worker.

Local accounts are kept by the control plane in `<deployments root>/users.json`, with scrypt
password hashes; sessions in `sessions.json`, by their token's hash. The workers never see users:
the control plane checks the caller, then calls the worker with the service token and the
caller's name in `X-Grayspace-User`.

| Route | |
|---|---|
| `GET /api/v1/auth/providers` | Sign-in sources (`local`; `oidc` later). No token needed |
| `POST /api/v1/auth/login` | A session for a local account. Five failures in a row, per account or per address, start a wait that doubles each time (429 with `Retry-After`) |
| `POST /api/v1/auth/logout` | End this session |
| `POST /api/v1/auth/password` | Change your password (12 characters or more); your other sessions end |
| `GET /api/v1/auth/sessions`, `DELETE .../{id}` | Your sessions; end one |
| `GET /api/v1/me` | Who you are: role, server-wide permissions, `must_change_password` |
| `GET, POST /api/v1/users`, `GET, PATCH, DELETE /api/v1/users/{username}` | Local accounts (admins). A new one gets a one-time password, shown once |
| `POST /api/v1/users/{username}/reset-password` | A new one-time password (admins); their sessions end |
| `GET /api/v1/directory` | Who can be added to a deployment: username, name and role (builders and admins) |
| `GET /api/v1/audit` | Every write through the server and every sign-in, newest first (admins) |
| `GET /api/v1/nodes` | The Proxmox nodes of every connection, and where adopted trees run: capacity, deployments, whether a job holds the cluster's lock (admins) |
| `/api/v1/platform[...]` | The Proxmox and Crucible connections deployments run on (see [Platform connections](#platform-connections)) |

- **The first admin.** `GRAYSPACE_BOOTSTRAP_ADMIN=<username>` in the environment file creates the
  account on first start. Its one-time password is in the server's log; it must be changed at
  first sign-in, and until it is, only `/me`, `/auth/password` and `/auth/logout` work. More
  accounts: Admin > Users in the UI, or `task user:create NAME=<name> ROLE=admin|builder|user`
  (`task user:reset-password NAME=<name>` too; in the container, `docker compose exec grayspace task ...`).
- **Disabling** an account, changing its role or resetting its password ends its sessions.
- **OIDC** (Keycloak, Crucible's identity server) comes later and ends in the same kind of session.

## Access

Roles are the server's:

- **admin**: everything, on every deployment, and the accounts, the audit log and the nodes.
- **builder**: creates deployments, and builds and deploys the ones they own or have build access to.
- **user**: views and uses the deployments they are a member of, but never changes what is deployed.

A deployment's creator is its **owner**. The owner (or an admin) adds **members**, with `build`
access (builders only) or `use` access: `PUT /api/v1/deployments/{id}/members`, or the deployment's
Settings in the UI. The UI's admin area (the user menu, for admins) manages users, and shows the
API keys, the image and role catalog, the nodes and the audit log. Anyone else can't
see the deployment: it is missing from the list and its routes are 404. Each deployment's
`my_access` says what the caller may do there:

| Permission | What it allows | Who |
|---|---|---|
| `read` | Every `GET`, the topology and events websockets | members, owner |
| `operate` | Consoles and desktops; instance actions (link conditions, DNS records, host stop and start); traffic shaping; interface zones; DNS and certificate changes; Reset changes (`POST /api/v1/changes/discard`); applying presets | members, owner |
| `build` | Deploy and configure the base; enclaves; creating and destroying instances; job cancel and retry; saving and deleting presets; images; base container power; restarting the worker | build members, owner |
| `members:manage`, `delete` | Rename, members, where it runs (connection, node) and its Player view; destroy the base; remove the deployment | owner |

The control plane classes every worker route (`control/access.py`) and refuses with 403 what the
caller's access doesn't allow; a test fails when a worker route isn't in the table. Making a
deployment the default is for admins. Deployments from before owners get the bootstrap admin as
their owner on first start.

## The deployments resource

`/api/v1/deployments`, at the server's root, needs a token like every other route:

| Route | |
|---|---|
| `GET /api/v1/deployments` | The deployments you can see, with their worker's state, their base's status, a summary (live and failed instances, the active job, the last base job), owner, members and `my_access` |
| `POST /api/v1/deployments` | Create one (builders, admins): `{"id": "abd", "name": "Exercise 2", "placement": {"connection": "site", "node": "pve2"}}`. You become its owner |
| `GET /api/v1/deployments/{id}` | One deployment, with its work tree and the worker log's tail |
| `PATCH /api/v1/deployments/{id}` | Rename it, make it the default (admins), or (while nothing is deployed) change where it runs or its Player view |
| `PUT /api/v1/deployments/{id}/members` | Who else may see and use it |
| `POST /api/v1/deployments/{id}/restart` | Restart its worker |
| `DELETE /api/v1/deployments/{id}` | Remove it; refused while it has a base or enclave instances |
| `/api/v1/deployments/events` | Websocket: the deployments you can see created, changed, removed, and their workers' state (`grayspace-deployments-events-v1.json`) |

From nothing to a second deployment's base:

```bash
H="Authorization: Bearer $GRAYSPACE_API_TOKEN"
curl -H "$H" -X POST https://grayspace.example/api/v1/deployments -d '{"id": "abd"}' -H 'Content-Type: application/json'
curl -H "$H" "https://grayspace.example/deployments/abd/api/v1/base/preflight?version=grayspace/2026.11&scope=tier1"
curl -H "$H" -X POST https://grayspace.example/deployments/abd/api/v1/base/deploy -d '{"version": "grayspace/2026.11", "scope": "tier1"}' -H 'Content-Type: application/json'
```

- **IDs.** The ID is the deployment's `environment_id`, which prefixes every Proxmox resource it
  creates. It is 1 to 4 lowercase letters and digits and starts with a letter. Four is the most
  because Proxmox SDN IDs are at most 8 characters, and slice vnets add 4 to the ID. The
  preflight's `environment-id` check refuses an ID that another deployment already uses on the
  cluster.
- **Settings.** Every deployment's environment file sources the site's (`.environment`, or
  `/config/environment` in the container), then sets its own values:
  - `placement`: a Proxmox connection and one of its nodes (the default connection and its default
    node when unset). The connection's host, port and credentials, and the node's storage and
    bridges, replace the site's: the file unsets the site's Proxmox settings first, so no site
    credential reaches another cluster.
  - `options`: `player` (a Crucible Player view) with `player_connection`
    when there is more than one Crucible connection. `use_custom_image` is accepted and ignored
    (contract 1.12): every Alpine guest uses the GraySpace image
  - `router_password`, write-only: only whether it is set is returned

  `proxmox` and `credentials` from before connections are refused (422). The response's `proxmox`
  is where the deployment runs, resolved from its placement.

## Platform connections

Admins define the Proxmox clusters and Crucible instances once, in Admin > Platform; deployments
choose one. Builders read them (to choose), admins (`platform:manage`) change them:

| Route | |
|---|---|
| `GET /api/v1/platform` | Both lists, with the deployments using each. Secrets are named, never returned |
| `PUT /api/v1/platform/defaults` | `{"default_proxmox": "<id>"}`: the connection new deployments get |
| `POST /api/v1/platform/proxmox` | `{"id", "name", "host", "port", "auth": "token"\|"password", "api_token" or "username"/"password", "ssh_username", "ssh_password", "nodes": [{"name", "blockstore", "filestore", "mgmt_bridge", "vm_bridge"}], "default_node", "apply_parallelism", "plan_parallelism", "proxy_mode": "site"\|"none"\|"custom", "proxy", "no_proxy"}` |
| `PATCH, DELETE /api/v1/platform/proxmox/{id}` | Fields left out stay; a secret sent empty is cleared |
| `POST /api/v1/platform/proxmox/{id}/test` | The API and the credentials, each node's storage and bridges, SSH |
| `GET /api/v1/platform/proxmox/{id}/discover` | The cluster's nodes, storage, bridges and SDN vnets, to fill the node form |
| `POST, PATCH, DELETE /api/v1/platform/crucible[/{id}]` | `{"id", "token_url", "auth_url", "client_id", "client_scopes", "username", "password", "player_api_url", "vm_api_url", "caster_api_url", "service_user_id", "player_url"}`; `player_url` is Player's UI, for the views' links, when it isn't `player_api_url` without `/api` |
| `POST /api/v1/platform/crucible/{id}/test` | A token from Keycloak for the account, and the Player API accepting it |

- **Kept in** `<deployments root>/platform.json`, and the secrets in `platform-secrets.json` (0600).
- **On first start** the site file's Proxmox settings become the connection `site` (and its
  Crucible settings the Crucible connection `site`, when `TF_VAR_configure_crucible` is true).
  Deployments from before get a placement: one on the site's host keeps `site`, with its node added;
  one with other storage on the same node, or another cluster, gets a connection of its own. One on
  another cluster without its own credentials (it used the site's) gets none, and a warning: give
  the connection credentials. The site file's Proxmox and Crucible lines are then ignored for
  managed deployments; adopted trees keep their own environment file.
- **Changes.** A connection deployments use can't be deleted, nor a node one uses removed. The host
  and port, and a used node's storage and bridges, change only while those deployments have nothing
  deployed. New credentials reach them at once: their environment files are written again and their
  workers restart, after the running job when one runs (`restart_pending`).
- **Parallelism.** `apply_parallelism` is Terraform's `-parallelism` for the applies and destroys of
  the deployments on the cluster: how many guests are created at once. Each create takes the
  storage's cluster lock (`cfs-lock 'storage-<id>'`) and gives up after about 10 seconds of waiting,
  so too many at once fail with `got lock request timeout`; busy shared storage needs fewer.
  `plan_parallelism` is for plans, which only read. An apply until converged plans to a file at
  `plan_parallelism`, refreshing the state there, and applies that file at `apply_parallelism`, so
  the slower apply only makes changes. Unset (or sent as 0), the server's defaults
  apply: `TERRAFORM_APPLY_PARALLELISM` (10) and `TERRAFORM_PLAN_PARALLELISM` (30). The deployment's
  environment file exports them as `GRAYSPACE_APPLY_PARALLELISM` and `GRAYSPACE_PLAN_PARALLELISM`,
  which jobs and `task apply` read; they change any time, and the next job uses them.
- **The guests' proxy.** What the guests of the cluster's deployments install packages through
  while Ansible configures them (over management; `ansible/inventory/proxy.ini`). `proxy_mode`
  `site` (the default) takes the site file's `TF_VAR_proxy` and `TF_VAR_no_proxy`; `none` exports
  them empty, so guests go direct even where the site has a proxy; `custom` exports `proxy` (an
  `http://` or `https://` URL) and `no_proxy`. Where `TF_VAR_proxy` isn't set at all, guests use the
  controller's own `http_proxy`, `https_proxy` and `no_proxy`, as before. It is only the guests':
  Terraform and the server reach Proxmox and the internet as their own environment says, and a node
  that downloads images uses the cluster's (`datacenter.cfg`). It changes any time; the next
  configure uses it.
- **A Player view.** With `options.player`, the deployment's environment file carries the Crucible
  connection's settings and `GRAYSPACE_PLAYER=true`; the builder's Player step then defines the view
  ([The Player view](#the-player-view)). Turning it on is allowed any time; turning it off only once
  the view is gone (409 otherwise: deploy the draft without a Player view first).

## Building a deployment: the draft and the rollout

A deployment's **draft** is what it should have: a base (version, scope, whether to configure)
and the enclave instances (slug, version, prefix). The UI's builder saves it; nothing changes
until it is deployed. Under the deployment's base URL:

| Route | |
|---|---|
| `GET /api/v1/draft` | The saved draft. Before anyone saves one, a draft of what is deployed (`adopted: true`) |
| `PUT /api/v1/draft` | Save it; unknown versions, enclaves and repeated prefixes are refused (422) |
| `GET /api/v1/deploy/plan` | What deploying would do, the stages in order, `problems` that stop it, and the node's capacity |
| `POST /api/v1/deploy` | Queue a **rollout** job (build access) |

The UI's builder (Build, and Configuration once the base is live) edits the draft in five steps:
Base, Enclaves (each spec in a form or as YAML; the form asks for the `role_vars` of the roles its
hosts use, once per role, since every host with a role reads the same values), DNS (the enclaves' `dns`), Certificates (their
`tls`) and Review & deploy. Before the base is deployed, it checks enclaves against the draft's
base version with `POST /api/v1/enclaves/{slug}/validate?base=<version>&scope=<scope>`.

A rollout's stages are `destroy:<id>` for the instances the draft no longer has (or has at
another version, or that failed), then `base` when the draft's base differs from the deployed one,
then `create:<id>` for each instance to build. Each stage runs a child job (`destroy_instance`,
`deploy_base`, `create_instance`) with its own record and log; the rollout's `children` lists them
and `GET /api/v1/jobs?parent=<id>` returns them. It stops at the first child that fails; retrying
the rollout resumes there, the child at the stage it failed. Cancelling it cancels the running child.
A rollout carries out the plan of the draft it deployed, so it resumes only while the draft is still
that one (who saved it and when don't count): after the draft changes, a retry is refused and Deploy
plans the draft as it is. The plan's `resumable_rollout` names the last rollout while it can be
retried. Review shows a failed rollout only then: one Retry can't help is left to Jobs.

Only instances the draft **manages** are ever destroyed: those rollouts created, and the ones the
first draft was made from. An instance stops being managed when it is destroyed, by a rollout or
not. Instances made outside it (by GHOSTS, or `POST /api/v1/instances`) are
listed as `unmanaged` and left alone; a draft prefix that one of them uses is a problem.

### Reset changes

Runtime changes (traffic shaping, interface zones, DNS domains and records,
certificates issued and revoked) are tracked, with who made each and when.
`GET /api/v1/changes` lists the ones since the active base preset; `POST /api/v1/changes/discard`
(Reset changes, operate access) undoes them back to that preset. A certificate issued is revoked; one revoked
stays revoked, since a CA can't take a revocation back. An instance's runtime actions are its own:
`POST /api/v1/instances/{id}/actions {"action": "reset"}` clears the link conditions actions set,
brings up the links they took down, deletes the DNS records they added and starts stopped hosts.
`POST /api/v1/reset/quick` and the `/api/v1/scenarios/changes`, `/scenarios/mutations/count` and
`/scenarios/discard-changes` routes are deprecated in favour of `/changes`.

### Presets

A preset is a named set of runtime changes, applied after a reset (`services/presets.py`; the web
UI's side is in docs/web-app.md, Presets). A preset belongs to a base version and is the
server's: every deployment on the version sees it. It is a list of actions in the shape
`POST /api/v1/base/actions` takes: `link.condition`, `interface.zone`, `dns.domain.create` and
`dns.record.set`. Every change to the base is made by
`services/base_changes.py`: the web UI's routes (links, interface zones, DNS) turn their
request into one of these actions, `POST /api/v1/base/actions` (operate access) takes one directly
(`link.clear` too), and applying a preset replays them, so a preset does what the UI does.

| Route | Access |
|---|---|
| `GET /api/v1/base/presets[?base=<version>]`, `GET /api/v1/base/presets/{name}` | read |
| `POST /api/v1/base/presets` (save the tracked changes), `PUT`, `DELETE /api/v1/base/presets/{name}` | build |
| `POST /api/v1/base/presets/{name}/apply` (`none`: the base as deployed) | operate |

The draft names one (`base.preset`; contract 1.8). The rollout adds a `preset` stage after the
base when the draft's preset differs from the one active or the base was just built. A null preset
leaves runtime changes alone.

**Enclaves have no presets** (contract 1.19). An enclave is what its spec builds: a variant of one
is another enclave. An instance's actions (link conditions, DNS records, stopping hosts) are live
changes, and its reset undoes them. The draft's `enclaves[].preset` is deprecated and ignored, the
`/api/v1/enclaves/{slug}/presets` and `/api/v1/instances/{id}/presets` routes are gone, and a
rollout planned before skips its `preset:<id>` stages. Files that were saved in
`<data>/slices/<slug>/presets/` are ignored; delete them if you like.

**Retired base versions.** `grayspace/2026.10` was retired with the base clients; `grayspace/2026.11`
is the same internet without them, so its routers, links, DNS servers and CAs, and the presets that
name them, are unchanged. A draft or preflight that names 2026.10 says what replaces it. Base presets
stay where they were saved: move them to the new version on the server, e.g.
`mv <deployments root>/presets/grayspace/2026.10 <deployments root>/presets/grayspace/2026.11`
(`/data/deployments` in the container; `deployments/` in a checkout). A preset that has a
`client.move` lists it in its `problems`; applying it applies the rest and reports that action as an error.

### The Player view

A deployment with the Player option has a Crucible Player view of its own, defined in its draft
(`player`, contract 1.10; the builder's Player step) and made in its own Terraform workspace,
`terraform/player/` (`services/player.py`):

```json
"player": {
  "view_name": "abc-grayspace", "description": "Exercise Bravo",
  "applications": [{"name": "VMs", "template_id": "e4698c62-..."}, {"name": "Chat", "url": "https://chat.example"}],
  "teams": [{"name": "Blue", "role": "View Member", "applications": ["VMs", "Chat"]}, {"name": "Red", "applications": ["VMs"]}],
  "default_team": "Blue",
  "assign": [{"scope": "base", "teams": ["Red"]}, {"scope": "enclave:mb", "teams": ["Blue"]}],
  "guests": [{"name": "lum-chi", "teams": ["Blue", "Red"]}, {"instance": "mb", "name": "ws", "teams": ["Red"]},
             {"instance": "mb", "name": "hq", "site": true, "teams": []}]
}
```

- **Guests** are base hostnames (routers, core services, `prometheus`), or an enclave's hosts and sites as its spec names them: a counted host is every copy,
  a site its edge router, whatever the instance's prefix.
- **Rules** (`assign`, contract 1.15) give every guest of the base (`base`) or of an enclave
  instance (`enclave:<prefix>`) to teams, including hosts a spec adds later. A guest in `guests`
  has the teams it names instead (`"teams": []` leaves it out); the builder lists only the guests
  that differ from their rule. A host or site the spec marks `crucible: true` also goes to
  `default_team`, unless `guests` names it.
- **Teams** get the view's applications in the order they list them. The Crucible connection's
  service user is admin of the view, through a team of its own, `Admin`. Nobody else is put on a
  team: who joins which team is for Player (or Scenario Builder).
- **The rollout** applies it in a `player` stage, last, once every VM exists, and whenever the
  draft's view changed or the base or an instance was rebuilt (VM IDs change). A draft without
  `player` removes the view, first. A base destroy removes it first too; an instance job outside a
  rollout updates it (`POST /instances`, `DELETE /instances/{id}`).
- **What the step offers:** `GET /api/v1/player/guests` (the base's guests by group and ISP, the
  draft's enclaves with their hints), `/player/templates` (Player's application templates),
  `/player/roles`; `GET /api/v1/player` is what was applied (`<data>/player.json`).
- **Before this workspace** the base module made the view (`modules/crucible`, one team Blue with
  every base guest, enclave guests through `terraform/slice/crucible.tf`). A deployment that still
  has it gets a draft `player` reproducing it; the first rollout destroys the old view (and the
  instances' registrations in it) through the provider's delete path, and makes this one. Applies
  refresh: the provider (2.7) manages a view's teams and applications as a whole, so it must see
  what was changed in Player itself (2.5.0 panicked reading a view back, and never refreshed).
  Destroys skip the refresh, as does that of a base whose state still holds Crucible resources.
  Teams and applications go to the provider in name order, as it matches them by position.
  An apply that adds applications runs twice: first with teams given only instances of the
  applications the view already has (`instance_applications`), then in full, as the provider (2.7,
  2.8) adds an instance of an application made in the same apply with an empty ID ("invalid UUID
  length: 0").

## How it runs

The server is a control plane (`grayspace-web/backend/control/`, `uvicorn control.main:app`). It
runs each deployment as its own backend process, the same backend a single-deployment GraySpace
runs, on a unix socket. The control plane keeps the registry, restarts workers that exit, and
forwards each deployment's routes to its worker.

Everything a deployment has is in its directory under the deployments root (`/data/deployments`
in the container, `deployments/` in a checkout, `aspire/.data/deployments` under `task dev`):

```
deployments/
  default                  the default deployment's ID
  locks/<proxmox host>.lock
  platform.json            the Proxmox and Crucible connections; platform-secrets.json their secrets
  presets/<base>/<version>/ base presets, shared by the deployments on each version
  <id>/deployment.json     the record
  <id>/secrets.json        write-only settings (the router password)
  <id>/environment         generated environment file (sources the site's)
  <id>/work/               the deployment's GraySpace tree: Terraform state, tfvars, inventory,
                           .ssh/, slice and image state, terraform/grayspace-data/
  <id>/run/                the worker's socket, log and pid
```

The work tree is a copy of GraySpace's code (`scripts/sync-worktree.sh`). The copy is refreshed on
every worker start and leaves state and generated files alone, so the usual tasks work in it too:

```bash
cd deployments/abd/work && task plan
docker compose -f container/compose.yaml exec -w /data/deployments/abd/work grayspace task plan   # in the container
```

### The existing deployment

On first start the control plane **adopts** the existing single-deployment tree as the default
deployment, under its own `TF_VAR_environment_id`. That tree is the devcontainer checkout, or the
container's `/data/work`. An adopted tree keeps its own environment file, state and data where
they were. The control plane never refreshes or deletes it; `DELETE` only forgets it.

### One job at a time per cluster

Proxmox applies SDN changes cluster-wide. Every deployment on a cluster therefore takes the same
lock, `locks/<proxmox host>.lock`, for its jobs: base deploys, configures and destroys, and enclave
instances. A long base deploy in one deployment holds up the other deployments' jobs on that
cluster until it finishes. Deployments on different clusters run their jobs in parallel.

Generated environment files set `GRAYSPACE_LOCK_FILE` to the cluster's lock, so `task apply` in a
work tree waits too. In the container, so does the entrypoint's `/data/work/.environment`, which
sources the site's file. An adopted checkout's own `.environment` doesn't set it. Until you add it
there (see `scripts/environment.template`), `task apply` in that checkout doesn't wait for the API's
jobs, and `GET /api/v1/deployments` warns about it.

Deployments the control plane runs also set `TF_VAR_write_home_ssh_key=false`, because they share
a user and would overwrite each other's `~/.ssh/terraform_key`. Each one's key is in its work
tree's `.ssh/`.

## Development

`task dev` runs the control plane with two simulated deployments, `dev` and `dev2`. Set
`GraySpace:Deployments` in `aspire/GraySpace.AppHost/appsettings.json` to change the list. Each
worker simulates the topology and has its own data. The router-metrics simulator follows the
first deployment's stopped guests.

```bash
curl -H 'Authorization: Bearer dev-token' localhost:8000/api/v1/deployments
curl localhost:8000/deployments/dev2/api/v1/topology/routers
```

The debugger attaches to the control plane, not to the workers it starts. To debug one worker,
set `GRAYSPACE_WORKER_DEBUG=<id>:<port>`, which runs that worker under `debugpy`
(`pip install debugpy`), and attach to the port.
