# Development

The Aspire AppHost in `aspire/GraySpace.AppHost` runs everything the web application needs, with
**F5** in VS Code: the API, the UI, and, without any Proxmox node, a simulated deployment with
metrics and Prometheus. It needs no container runtime; every service runs as a process.

## Run it

| How | |
|---|---|
| **F5** in VS Code | Pick **GraySpace (simulated)** or **GraySpace (deployment)** in Run and Debug. The API is the control plane ([Deployments](deployments.md)), under the debugger; each deployment's backend is its subprocess, which `GRAYSPACE_WORKER_DEBUG=<id>:<port>` runs under debugpy to attach to |
| `task dev` | The same from a terminal (`task dev MODE=deployment`); Ctrl+C stops everything |
| `dotnet run --project aspire/GraySpace.AppHost` | Without the Aspire CLI (`--launch-profile deployment` for the other mode) |

The devcontainer installs what F5 needs: the Aspire and C# extensions, and the Aspire CLI
(`.devcontainer/setup-aspire.sh`). In an older devcontainer, run that script once, or rebuild.
Behind a proxy, the AppHost adds the loopback addresses (`localhost`, `127.0.0.1`, `::1`, `[::1]`)
to `NO_PROXY` for itself and the services it starts: Aspire's orchestrator listens on `[::1]`, and a
`NO_PROXY` without it sends the AppHost's own calls to the proxy, so startup times out after a
minute.
The first start downloads Prometheus into `~/.cache/grayspace-dev`, and makes the
backend's Python environment (`grayspace-web/backend/.venv`, Python 3.10–3.13).

Open the **Aspire dashboard** at <http://localhost:18888> for every service's state, logs and
links. It asks for a login token, new on each start: follow the login link (`/login?t=...`) that
F5, `task dev` or `dotnet run` print when the AppHost starts. The services, on fixed ports (`aspire/GraySpace.AppHost/appsettings.json`), all forwarded by
the devcontainer:

| Service | URL | |
|---|---|---|
| ui | <http://localhost:3000> | The React app (`npm start`, hot reload). Sign in as `admin`, `builder` or `user`, password `grayspace-dev` (made on start); each sees what its role allows |
| api | <http://localhost:8000/api/docs> | The FastAPI backend (uvicorn `--reload`). `/api/docs` is Swagger for the Terrain API contract (GET-only Try it out unless `GRAYSPACE_API_DOCS_WRITES=true`); `/docs` and `/redoc` list every backend route, including the UI's own. Authorize with `dev-token` |
| router-metrics | <http://localhost:9342/status> | Simulated router metrics (below) |
| prometheus | <http://localhost:9090> | Scraping router-metrics |
| docs | <http://localhost:8001> | `mkdocs serve` |

## Simulated mode

No Proxmox and no routers, but the application behaves as if a core were deployed:

- **Two simulated deployments**, `dev` (the default, which the UI shows) and `dev2`, each its own
  worker with its own data (`GraySpace:Deployments` in `appsettings.json` changes the list; reach
  `dev2` at `/deployments/dev2/api/v1/...`). Each is a simulated deployment
  (`DEV_SIMULATE_DEPLOYMENT`, `services/dev_simulation.py`): the API reports every guest of the topology as a running container, so the map, the router, DNS and CA
  lists, and capacity all have content. Starting and stopping guests in the UI works, in
  memory. Nothing can be reached over SSH, so zone contents, certificates, consoles and traffic
  shaping report errors.
- **Simulated jobs** (`services/dev_jobs.py`): core deploys, configures and destroys, instance
  creates and destroys, plans and rollouts walk their stages, `DEV_SIMULATION_STAGE_SECONDS` each
  (1 by default). A core deploy writes the topology's tfvars and a destroy removes them; an
  instance is validated and allocated for real, so its hosts show on the map. `dev` and `dev2`
  start deployed; a deployment made later (`POST /api/v1/deployments`, or New deployment in the UI)
  starts empty, for the builder to deploy. `DEV_SIMULATION_FAIL=<kind>:<stage>` (e.g.
  `create_instance:configure`) fails that stage once, to try failures, retries and rollouts that stop.
- **Simulated router metrics** (`aspire/simulator/router_metrics.py`): the `frr_exporter` and
  `node_exporter` series (BGP sessions, OSPF adjacencies, interface states and traffic) of every
  router, which Prometheus scrapes, so link states on the map are live. Break things to see the UI
  react:

  ```bash
  curl -X POST localhost:9342/links/lum-seo:lum-tok/down     # both ends, OSPF and eBGP go down
  curl -X POST localhost:9342/routers/lum-chi/down           # the router stops reporting
  curl -X POST localhost:9342/reset                          # everything up (also a dashboard command)
  ```

  Stopping a router in `dev` takes it down in the metrics too (the simulator follows the first deployment). Link states reach the UI within
  about 10 seconds (5 s scrape, 5 s poll).

The topology is `terraform/terraform.tfvars.json` if `task parse` made one, else the golden
fixture of `grayspace/2026.11` (`tests/golden/tfvars-full.json`); `GraySpace:Tfvars` in
`appsettings.json` chooses another. Generated configuration and the services' data are in
`aspire/.data` (git-ignored; delete it to start clean).

## Deployment mode

The API against a real deployment, with the settings of `.environment` (`task env`): the Proxmox
API, the deployed `terraform/terraform.tfvars.json`, the slice data directory the core guard reads,
and the API token. Reach the management network first (`task tunnel`). The dashboard asks for the
deployment's Prometheus URL (or set `GraySpace:PrometheusUrl`). Slice jobs run from this checkout,
as the container runs them from its work trees; no local Prometheus or simulator is started. Sign in to the UI
with the bootstrap admin (`GRAYSPACE_BOOTSTRAP_ADMIN` in `.environment`; its one-time password is
in the api's log on first start) or an account from `task user:create NAME=<name> ROLE=admin`.

## The slices demo

`task demo` (`scripts/demo_slices.py`) is a narrated walk through slices: the core, a spec, its
allocation and rendering, findings for a broken spec, and many instances sharing the core. Those
acts run the backend's code in-process and need nothing running. With the simulated stack up, a
last act stores the example slices through the API and stages three instances in `aspire/.data`, so
they appear on the map and under Instances. It also cuts lum-chi off in the router-metrics simulator
for a while. Only validate, allocate and render really run there; the stages that need Proxmox are
marked as simulated. `task demo -- --auto` advances on a timer, `-- --offline` skips the live act,
and `-- --clean` removes what the live act staged.

## Notes

- The UI's websockets (`/ws`, consoles, desktops, `/api/v1/events`) go to the API directly in
  development (`REACT_APP_WS_URL`, `.env.development`), because the dev server keeps `/ws` for hot
  reload; HTTP `/api` goes through the dev server's proxy (`src/setupProxy.js`). A deployment
  serves both from one origin through nginx.
- Tests: `grayspace-web/backend/.venv/bin/python -m unittest discover -s tests -t .` in
  `grayspace-web/backend` (the venv the AppHost makes), and `python -m unittest discover -s tests -t .`
  at the root.
