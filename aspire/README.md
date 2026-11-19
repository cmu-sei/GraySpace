# Aspire AppHost for development

F5 in VS Code (or `task dev`) runs the API, the UI and the infrastructure they need, with no
Proxmox and no container runtime. See [docs/development.md](../docs/development.md).

| Path | |
|---|---|
| `GraySpace.AppHost/` | The AppHost: `AppHost.cs` (the services), `DevEnvironment.cs` (paths, `.environment`, generated Prometheus config), `appsettings.json` (mode, ports), launch profiles `simulated` and `deployment` |
| `simulator/router_metrics.py` | Simulated `frr_exporter` and `node_exporter` metrics for every router, with link and router failures on demand |
| `scripts/install-tool.sh` | Downloads Prometheus once into `~/.cache/grayspace-dev` |
| `.data/` | Generated config and the local services' data (git-ignored) |

The backend's Python environment comes from `scripts/backend-venv.sh` (also `task backend:venv`).
