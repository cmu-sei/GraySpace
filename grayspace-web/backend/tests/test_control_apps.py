"""GET /api/apps: the apps for the header's app switcher, from the control plane's settings."""

import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from control.settings import ControlSettings, settings
from tests import AUTH


class SwitcherApps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from control.main import app

        # No context manager: the lifespan (workers, adoption) isn't needed for this route
        cls.client = TestClient(app)

    def test_returns_the_configured_apps_in_order(self):
        configured = [
            {"Id": "scenario-builder", "Name": "Scenario Builder", "Url": "http://localhost:4310"},
            {"Id": "grayspace", "Name": "GraySpace"},
            {"Id": "chronicle", "Name": "Chronicle", "Url": "http://localhost:4320"},
            {"Id": "ghosts", "Name": "GHOSTS"},
        ]
        with patch.dict(os.environ, {"GRAYSPACE_APPS": json.dumps(configured)}):
            apps = ControlSettings().apps
        with patch.object(settings, "apps", apps):
            response = self.client.get("/api/apps", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [
            {"id": "scenario-builder", "name": "Scenario Builder", "url": "http://localhost:4310"},
            {"id": "grayspace", "name": "GraySpace"},
            {"id": "chronicle", "name": "Chronicle", "url": "http://localhost:4320"},
            {"id": "ghosts", "name": "GHOSTS"},
        ])

    def test_the_default_list(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GRAYSPACE_APPS", None)
            apps = ControlSettings().apps
        self.assertEqual([a.id for a in apps], ["scenario-builder", "grayspace", "chronicle", "ghosts"])
        self.assertEqual([a.url for a in apps], ["http://localhost:4310", None, "http://localhost:4320", None])

    def test_needs_a_session(self):
        self.assertEqual(self.client.get("/api/apps").status_code, 401)


if __name__ == "__main__":
    unittest.main()
