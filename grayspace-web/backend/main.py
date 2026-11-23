"""GraySpace Web Application - FastAPI Backend."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import topology, websocket, containers, traffic, console, desktop, networks, reset, link_traffic, interface_zones, dns, ca, changes, presets, base, base_actions, capabilities, player
from routers import api_docs, ca_bundle, events, images, instances, jobs, map as map_router, rollout, slices, summary
from config import settings
from services.auth import TokenAuthMiddleware
from services.request_user import RequestUserMiddleware
import asyncio
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Create FastAPI application
app = FastAPI(
    title="GraySpace Management API",
    description="API for managing and visualizing GraySpace network simulation",
    version="1.0.0"
)

# Token auth on write routes and console/desktop sessions (services/auth.py). CORS is added
# after it so that it wraps it, and 401s still carry CORS headers.
app.add_middleware(TokenAuthMiddleware)
# Who the control plane says made the request, for attribution (services/request_user.py)
app.add_middleware(RequestUserMiddleware)

# Only the configured origins (GRAYSPACE_CORS_ORIGINS) may call the API from a browser. The token
# travels in a header, not a cookie, so credentials are not allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

# Include routers
app.include_router(topology.router)
app.include_router(websocket.router)
app.include_router(containers.router)
app.include_router(traffic.router)
app.include_router(console.router)
app.include_router(desktop.router)
app.include_router(networks.router)
app.include_router(reset.router)
app.include_router(link_traffic.router)
app.include_router(interface_zones.router)
app.include_router(dns.router)
app.include_router(ca.router)
app.include_router(changes.router)
app.include_router(changes.legacy_router)
app.include_router(presets.router)
app.include_router(presets.legacy_router)
app.include_router(base_actions.router)
app.include_router(base_actions.legacy_router)
app.include_router(player.router)
app.include_router(base.router)
app.include_router(base.legacy_router)
app.include_router(capabilities.router)
app.include_router(slices.router)
app.include_router(slices.legacy_router)
app.include_router(instances.router)
app.include_router(jobs.router)
app.include_router(images.router)
app.include_router(map_router.router)
app.include_router(events.router)
app.include_router(ca_bundle.router)
app.include_router(summary.router)
app.include_router(rollout.router)
# Swagger for the Terrain API contract at /api/docs
app.include_router(api_docs.router)
api_docs.mount_assets(app)


@app.on_event("startup")
async def startup_event():
    """Application startup tasks."""
    logger.info("=" * 80)
    logger.info("GraySpace Web Application Starting")
    logger.info("=" * 80)
    logger.info(f"Environment ID: {settings.environment_id}")
    logger.info(f"Tfvars Path: {settings.tfvars_path}")
    logger.info(f"Prometheus URL: {settings.prometheus_url}")
    logger.info(f"Proxmox Host: {settings.proxmox_api_host}")
    logger.info(f"Proxmox Node: {settings.proxmox_node}")
    if not settings.api_token:
        logger.warning("GRAYSPACE_API_TOKEN is not set: the Terrain API and every write route will refuse requests")
    logger.info("=" * 80)

    # Load persisted mutations
    from services.mutation_tracker import mutation_tracker
    from services.topology_service import topology_service
    mutation_tracker.load()
    mutation_tracker.validate_against_tfvars(topology_service.tfvars_data)

    # Job runner for base and slice jobs (only where a GraySpace checkout can run Terraform and Ansible)
    from services import base_jobs, rollout  # noqa: F401  (registers the base job and rollout kinds)
    from services.slices import stages  # noqa: F401  (registers the job kinds)
    from services.slices.runner import runner
    from services import dev_simulation
    if dev_simulation.enabled():
        from services import dev_jobs
        dev_jobs.register()
    await runner.start()

    # Start state monitoring background task
    from tasks.state_monitor import state_monitor
    asyncio.create_task(state_monitor.start())
    logger.info("State monitor background task started")

    # Start console manager
    from services.console_service import console_manager
    await console_manager.start()
    logger.info("Console manager started")


@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown tasks."""
    from services.prometheus_client import prometheus_client
    from services.console_service import console_manager
    from tasks.state_monitor import state_monitor

    # Stop state monitor
    await state_monitor.stop()

    from services.slices.runner import runner
    await runner.stop()

    # Stop console manager
    await console_manager.stop()

    # Close clients
    await prometheus_client.close()

    logger.info("GraySpace Web Application Shutdown")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "GraySpace Management API",
        "version": "1.0.0",
        "environment_id": settings.environment_id
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
