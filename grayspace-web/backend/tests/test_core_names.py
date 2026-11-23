"""Contract 1.26: the base is called the core. The core names are the API's, and the base ones stay as
deprecated aliases until 2.0: the /api/v1/base routes, and the base fields beside each core one."""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import contract
from control import access
from control.models import DeploymentSummary
from models.rollout import Draft, DraftBase, DraftUpdate, DeployPlan, PlannedBase
from routers import base
from services import base_versions
from tests import AUTH

CORE = DraftBase(version="grayspace/2026.11", scope="full", configure=True)


class Routes(unittest.TestCase):
    def test_the_base_routes_are_the_core_routes(self):
        app = FastAPI()
        app.include_router(base.router)
        app.include_router(base.legacy_router)
        client = TestClient(app, headers=AUTH)
        with patch.object(base_versions.topology_service, "tfvars_data", {}):
            core, old = client.get("/api/v1/core/versions"), client.get("/api/v1/base/versions")
        self.assertEqual(core.status_code, 200)
        self.assertEqual(core.json(), old.json())

    def test_the_contract_marks_the_base_routes_deprecated(self):
        paths = contract.contract_app().openapi()["paths"]
        for path, item in paths.items():
            for method, op in item.items():
                if not isinstance(op, dict) or "responses" not in op:
                    continue
                if path.startswith("/api/v1/base"):
                    self.assertTrue(op.get("deprecated"), f"{method} {path}")
                    self.assertIn(path.replace("/api/v1/base", "/api/v1/core", 1), paths)
                elif path.startswith("/api/v1/core"):
                    self.assertFalse(op.get("deprecated"), f"{method} {path}")

    def test_both_names_have_the_same_access(self):
        for method, path in [("POST", "/api/v1/core/deploy"), ("POST", "/api/v1/core/configure"), ("DELETE", "/api/v1/core"),
                             ("POST", "/api/v1/core/actions"), ("POST", "/api/v1/core/presets"),
                             ("PUT", "/api/v1/core/presets/slow"), ("POST", "/api/v1/core/presets/slow/apply")]:
            old = path.replace("/api/v1/core", "/api/v1/base", 1)
            self.assertIsNotNone(access.listed_class(method, path), path)
            self.assertEqual(access.route_class(method, path), access.route_class(method, old), path)


class Draft_(unittest.TestCase):
    def test_takes_core_or_base_and_answers_both(self):
        for body in ({"core": CORE.model_dump()}, {"base": CORE.model_dump()}, {"core": CORE.model_dump(), "base": CORE.model_dump()}):
            dumped = DraftUpdate.model_validate(body).model_dump(mode="json")
            self.assertEqual(dumped["core"], CORE.model_dump(mode="json"), body)
            self.assertEqual(dumped["base"], dumped["core"], body)

    def test_core_and_base_that_differ_are_refused(self):
        with self.assertRaises(ValidationError):
            DraftUpdate(core=CORE, base=CORE.model_copy(update={"scope": "tier1"}))

    def test_a_stored_draft_from_before_reads_the_same(self):
        old = {"base": CORE.model_dump(mode="json"), "enclaves": [], "player": None}
        self.assertEqual(Draft.model_validate(old).core, CORE)
        self.assertEqual(Draft.model_validate(old).model_dump(), Draft.model_validate(Draft.model_validate(old).model_dump()).model_dump())


class Responses(unittest.TestCase):
    def test_the_plan_names_the_core_twice(self):
        plan = DeployPlan(base=PlannedBase(action="deploy", version="grayspace/2026.11"), instances=[], changes=True)
        dumped = plan.model_dump(mode="json")
        self.assertEqual(dumped["core"], dumped["base"])

    def test_the_summary_names_the_last_core_job_twice(self):
        dumped = DeploymentSummary(last_base_job=None).model_dump(mode="json")
        self.assertIn("last_core_job", dumped)
        self.assertIn("last_base_job", dumped)

    def test_the_contract_has_each_core_field_beside_its_deprecated_base_one(self):
        schemas = contract.contract_app().openapi()["components"]["schemas"]
        pairs = {"Draft": ["base"], "DraftUpdate": ["base"], "DeployPlan": ["base"], "Deployment": ["base"],
                 "DeploymentSummary": ["last_base_job"], "Capabilities": ["base", "base_jobs", "base_jobs_unavailable_reason"],
                 "Guests": ["base"], "InstanceView": ["base"]}
        for schema, olds in pairs.items():
            props = schemas[schema]["properties"]
            for old in olds:
                new = old.replace("base", "core")
                self.assertIn(new, props, f"{schema}.{new}")
                self.assertTrue(props[old].get("deprecated"), f"{schema}.{old}")
                self.assertFalse(props[new].get("deprecated"), f"{schema}.{new}")


if __name__ == "__main__":
    unittest.main()
