"""Swagger for the contract at /api/docs (routers/api_docs.py)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import contract
from config import settings
from routers import api_docs
from services.auth import TokenAuthMiddleware


def client():
    app = FastAPI()
    app.add_middleware(TokenAuthMiddleware)
    app.include_router(api_docs.router)
    api_docs.mount_assets(app)
    return TestClient(app)


class ApiDocs(unittest.TestCase):
    def test_the_spec_is_the_contract_and_needs_no_token(self):
        r = client().get("/api/openapi.json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), contract.contract_app().openapi())
        self.assertIn("From nothing deployed to a core and a slice", r.json()["info"]["description"])

    def test_try_it_out_sends_only_gets_by_default(self):
        html = client().get("/api/docs").text
        self.assertIn('"supportedSubmitMethods": ["get"]', html)
        self.assertIn("/api/openapi.json", html)
        self.assertIn("cdn.jsdelivr.net/npm/swagger-ui-dist@5", html)

    def test_writes_can_be_enabled(self):
        with patch.object(settings, "api_docs_writes", True):
            html = client().get("/api/docs").text
        self.assertNotIn("supportedSubmitMethods", html)

    def test_swagger_files_from_the_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "swagger-ui-bundle.js").write_text("// bundle")
            with patch.object(settings, "swagger_ui_path", tmp):
                c = client()
                html = c.get("/api/docs").text
                self.assertIn("/api/docs/assets/swagger-ui-bundle.js", html)
                self.assertEqual(c.get("/api/docs/assets/swagger-ui-bundle.js").text, "// bundle")

    def test_the_page_is_not_in_any_spec(self):
        self.assertNotIn("/api/docs", contract.contract_app().openapi()["paths"])


if __name__ == "__main__":
    unittest.main()
