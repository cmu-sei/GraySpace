// The GraySpace development AppHost (aspire/README.md). F5 in VS Code, or:
//
//   dotnet run --project aspire/GraySpace.AppHost                          # simulated (no Proxmox)
//   dotnet run --project aspire/GraySpace.AppHost --launch-profile deployment
//
// Simulated mode runs, with no Proxmox and no container runtime:
//   api            grayspace-web's control plane (control.main:app, docs/deployments.md), running a
//                  backend worker per simulated deployment of the topology (GraySpace:Deployments,
//                  dev and dev2 by default): every guest reports as running
//   ui             the React app (npm start), proxying /api to the api; websockets go to the api directly
//   router-metrics frr_exporter and node_exporter series for every router, with link and router
//                  failures you can trigger (POST /links/<r1:r2>/down, /routers/<host>/down)
//   prometheus     scraping router-metrics, as the deployed Prometheus scrapes the routers
//   docs           mkdocs serve
// Deployment mode runs the api (and ui, docs) against a real deployment with .environment: the
// control plane adopts the checkout as the default deployment, and makes others in deployments/.
// The workers are the control plane's subprocesses, so the debugger attaches to the control plane;
// GRAYSPACE_WORKER_DEBUG=<id>:<port> runs one worker under debugpy (pip install debugpy).

using Microsoft.Extensions.Configuration;

// WithoutHttpsCertificate is still experimental (Aspire 17); it is how a resource opts out of the
// developer certificate, which the Aspire CLI would otherwise give the Python API.
#pragma warning disable ASPIRECERTIFICATES001

// Aspire's orchestrator (DCP) listens on https://[::1]:<port>. Behind a proxy whose NO_PROXY lists
// localhost and 127.0.0.1 but not ::1, the AppHost would send its own calls to DCP to the proxy and
// time out at startup. Exempt the loopback addresses for the AppHost and everything it starts. .NET
// matches IPv6 hosts with their brackets ([::1]); curl and Python match the bare form.
foreach (var name in new[] { "NO_PROXY", "no_proxy" })
{
    var hosts = (Environment.GetEnvironmentVariable(name) ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToList();
    foreach (var loopback in new[] { "localhost", "127.0.0.1", "::1", "[::1]" })
    {
        if (!hosts.Contains(loopback)) hosts.Add(loopback);
    }
    Environment.SetEnvironmentVariable(name, string.Join(",", hosts));
}

var builder = DistributedApplication.CreateBuilder(args);
var dev = new DevEnvironment(builder.AppHostDirectory);
var config = builder.Configuration.GetSection("GraySpace");
var mode = (config["Mode"] ?? "simulated").ToLowerInvariant();
if (mode is not ("simulated" or "deployment"))
{
    throw new InvalidOperationException($"GraySpace:Mode is '{mode}'; use simulated or deployment");
}
var simulated = mode == "simulated";
int Port(string name) => config.GetValue<int>($"Ports:{name}");
string Local(string name) => "http://localhost:" + Port(name);

// --- The backend's Python environment (Python 3.10-3.13: pydantic 2.9 has no 3.14 build) ---

var backendVenv = builder.AddExecutable("backend-venv", "bash", dev.Root, dev.Repo("scripts", "backend-venv.sh"));

// --- The API ---

var api = builder.AddUvicornApp("api", dev.Backend, "control.main:app")
    .WithVirtualEnvironment(".venv", createIfNotExists: false)
    // backend-venv installs the requirements with a Python the pinned pydantic supports
    .WithPip(install: false)
    // Plain HTTP, as nginx serves the backend in a deployment; the UI proxies to it.
    .WithoutHttpsCertificate()
    .WithEndpoint("http", e =>
    {
        e.UriScheme = "http";
        e.Port = Port("Api");
        e.TargetPort = Port("Api");
        e.IsProxied = false;
    })
    .WithDebugging()
    .WithHttpHealthCheck("/health")
    .WithUrls(context =>
    {
        context.Urls.Add(new() { Url = Local("Api") + "/docs", DisplayText = "Swagger" });
        context.Urls.Add(new() { Url = Local("Api") + "/redoc", DisplayText = "ReDoc" });
    })
    .WaitForCompletion(backendVenv)
    .WithEnvironment("PYTHONUNBUFFERED", "1")
    .WithEnvironment("GRAYSPACE_SOURCE_PATH", dev.Root)
    // Workers inherit it
    .WithEnvironment("STATE_POLL_INTERVAL", "5")
    .WithEnvironment("GRAYSPACE_CORS_ORIGINS", Local("Ui"));

if (simulated)
{
    var tfvars = dev.SimulatedTfvars(config["Tfvars"]);
    var token = builder.AddParameter("api-token", "dev-token", secret: true);
    var stoppedFile = dev.DataPath("backend", "dev-simulation-stopped.json");

    var routerMetrics = builder.AddExecutable("router-metrics", DevEnvironment.Which("python3") ?? "python3", dev.Repo("aspire", "simulator"), "router_metrics.py")
        .WithHttpEndpoint(port: Port("RouterMetrics"), env: "PORT", isProxied: false)
        .WithEnvironment("TFVARS", tfvars)
        .WithEnvironment("STATE_FILE", stoppedFile)
        .WithEnvironment("PYTHONUNBUFFERED", "1")
        .WithEnvironment("SIM_QUIET", "1")
        .WithHttpHealthCheck("/status")
        .WithHttpCommand("/reset", "Reset simulated network", commandOptions: new() { Method = HttpMethod.Post, IconName = "ArrowReset" })
        .WithUrls(context => context.Urls.Add(new() { Url = Local("RouterMetrics") + "/status", DisplayText = "Status" }));

    var prometheusInstall = builder.AddExecutable("prometheus-install", "bash", dev.Root, dev.Repo("aspire", "scripts", "install-tool.sh"), "prometheus", "3.6.0");
    var prometheusConfig = dev.WritePrometheusConfig(Port("RouterMetrics"), Port("Prometheus"));
    var prometheus = builder.AddExecutable("prometheus", Path.Combine(dev.Cache, "prometheus-3.6.0", "prometheus"), dev.DataDir("prometheus"),
            $"--config.file={prometheusConfig}",
            $"--storage.tsdb.path={dev.DataDir("prometheus", "data")}",
            "--storage.tsdb.retention.time=1d",
            $"--web.listen-address=0.0.0.0:{Port("Prometheus")}")
        .WithHttpEndpoint(port: Port("Prometheus"), isProxied: false)
        .WithHttpHealthCheck("/-/ready")
        .WaitForCompletion(prometheusInstall)
        .WaitFor(routerMetrics);

    api.WithEnvironment("GRAYSPACE_SIMULATE", "true")
        .WithEnvironment("GRAYSPACE_SIMULATE_TFVARS", tfvars)
        // The first deployment's stopped guests are the ones router-metrics fails
        .WithEnvironment("GRAYSPACE_SIMULATE_STATE_FILE", stoppedFile)
        .WithEnvironment("GRAYSPACE_BOOTSTRAP_DEPLOYMENTS", config["Deployments"] ?? "dev,dev2")
        .WithEnvironment("GRAYSPACE_DEPLOYMENTS_PATH", dev.DataDir("deployments"))
        .WithEnvironment("PROMETHEUS_URL", Local("Prometheus"))
        .WithEnvironment("GRAYSPACE_API_TOKEN", token)
        // The UI's sign-in: admin, builder and user, all with the password grayspace-dev
        .WithEnvironment("GRAYSPACE_SEED_USERS", "admin:admin,builder:builder,user:user")
        .WithEnvironment("GRAYSPACE_SEED_PASSWORD", "grayspace-dev")
        .WaitFor(prometheus);
}
else
{
    var environment = dev.ReadEnvironmentFile()
        ?? throw new InvalidOperationException("Deployment mode needs .environment in the repository root (task env)");
    var values = environment.Values;
    string Required(string name) => values.TryGetValue(name, out var v) && v.Length > 0
        ? v
        : throw new InvalidOperationException($"{environment.Path} doesn't set {name}");

    var prometheusUrl = string.IsNullOrWhiteSpace(config["PrometheusUrl"])
        ? builder.AddParameter("prometheus-url")
            .WithDescription("The deployment's Prometheus, e.g. http://10.0.1.20:9090 (task tunnel reaches the management network)")
        : builder.AddParameter("prometheus-url", config["PrometheusUrl"]!);
    var token = builder.AddParameter("api-token", Required("GRAYSPACE_API_TOKEN"), secret: true);

    api.WithEnvironment("GRAYSPACE_ADOPT_PATH", dev.Root)
        .WithEnvironment("GRAYSPACE_SITE_ENVIRONMENT", environment.Path)
        .WithEnvironment("GRAYSPACE_DEPLOYMENTS_PATH", dev.Repo("deployments"))
        // The adopted deployment's Prometheus; deployments made through the API find theirs in their inventory
        .WithEnvironment("PROMETHEUS_URL", prometheusUrl)
        .WithEnvironment("GRAYSPACE_API_TOKEN", token);
    // The first admin's one-time password is in the api's log (docs/deployments.md, Sign-in)
    if (values.TryGetValue("GRAYSPACE_BOOTSTRAP_ADMIN", out var bootstrapAdmin) && bootstrapAdmin.Length > 0)
        api.WithEnvironment("GRAYSPACE_BOOTSTRAP_ADMIN", bootstrapAdmin);
    // The checkout is adopted under its environment_id
    Required("TF_VAR_environment_id");
}

// --- The UI: the CRA dev server. /api goes through src/setupProxy.js; the dev server keeps /ws
// for hot reload, so the app's websockets go to the api directly. ---

builder.AddJavaScriptApp("ui", dev.Frontend, "start")
    .WithNpm(install: true)
    .WithHttpEndpoint(port: Port("Ui"), env: "PORT", isProxied: false)
    .WithEnvironment("GRAYSPACE_API_URL", Local("Api"))
    .WithEnvironment("REACT_APP_WS_URL", "ws://localhost:" + Port("Api"))
    .WithEnvironment("BROWSER", "none")
    .WaitFor(api);

// --- The docs ---

var mkdocs = DevEnvironment.Which(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".venv", "bin", "mkdocs"), "mkdocs");
if (config.GetValue("Docs", true) && mkdocs is not null)
{
    builder.AddExecutable("docs", mkdocs, dev.Root, "serve", "--dev-addr", $"0.0.0.0:{Port("Docs")}")
        .WithHttpEndpoint(port: Port("Docs"), isProxied: false);
}

builder.Build().Run();
