"""Swagger for the Terrain API contract, at /api/docs, with the spec at /api/openapi.json.

The page documents the contract (contract.py), not the routes private to the web UI, which the
backend's own /docs still lists in development. It's under /api so nginx forwards it in the
container and the LXC like any API route. It needs no token to view; Authorize stores one in the
browser for Try it out.

Try it out sends only GET requests unless GRAYSPACE_API_DOCS_WRITES is true: the page documents
deploy, destroy and every other write, but one click shouldn't destroy the base. Swagger UI's files
come from settings.swagger_ui_path when set (the container ships them, for sites without internet
access), otherwise from the CDN FastAPI uses by default.
"""
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings

router = APIRouter(include_in_schema=False)

SPEC_URL = "/api/openapi.json"
ASSETS_URL = "/api/docs/assets"
CDN = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5"


@lru_cache(maxsize=1)
def spec() -> dict:
    from contract import contract_app

    return contract_app().openapi()


def assets_dir():
    path = Path(settings.swagger_ui_path) if settings.swagger_ui_path else None
    return path if path and (path / "swagger-ui-bundle.js").is_file() else None


def mount_assets(app):
    """Serve Swagger UI's files from the image when it has them."""
    path = assets_dir()
    if path:
        app.mount(ASSETS_URL, StaticFiles(directory=str(path)), name="swagger-ui")


@router.get(SPEC_URL)
async def openapi_spec():
    return JSONResponse(spec())


@router.get("/api/docs")
async def swagger_ui() -> HTMLResponse:
    base = ASSETS_URL if assets_dir() else CDN
    parameters = {
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "docExpansion": "none",
        "tryItOutEnabled": False,
    }
    if not settings.api_docs_writes:
        parameters["supportedSubmitMethods"] = ["get"]
    return get_swagger_ui_html(
        openapi_url=SPEC_URL,
        title=f"{spec()['info']['title']} {spec()['info']['version']}",
        swagger_js_url=f"{base}/swagger-ui-bundle.js",
        swagger_css_url=f"{base}/swagger-ui.css",
        swagger_favicon_url=f"{base}/favicon-32x32.png",
        swagger_ui_parameters=parameters,
    )
