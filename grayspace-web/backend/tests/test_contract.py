"""openapi/ must match the contract, so GHOSTS never builds against a stale spec: the Terrain API
(grayspace-v1.json), the slice spec schema and the events schema."""

import json
import unittest

import contract

PATHS = {
    "/api/v1/core",
    "/api/v1/core/preflight",
    "/api/v1/core/deploy",
    "/api/v1/core/configure",
    "/api/v1/core/inventory",
    "/api/v1/core/dns",
    "/api/v1/core/versions",
    "/api/v1/core/versions/{name}",
    "/api/v1/core/preview",
    "/api/v1/base",
    "/api/v1/base/preflight",
    "/api/v1/base/deploy",
    "/api/v1/base/configure",
    "/api/v1/base/inventory",
    "/api/v1/base/dns",
    "/api/v1/base/versions",
    "/api/v1/base/versions/{name}",
    "/api/v1/base/preview",
    "/api/v1/draft",
    "/api/v1/deploy/plan",
    "/api/v1/deploy",
    # Since 1.10 (routers/player.py)
    "/api/v1/player",
    "/api/v1/player/templates",
    "/api/v1/player/roles",
    "/api/v1/player/guests",
    "/api/v1/capabilities",
    "/api/v1/attachment-points",
    "/api/v1/enclaves",
    "/api/v1/enclaves/{slug}",
    "/api/v1/enclaves/{slug}/validate",
    "/api/v1/enclaves/{slug}/plan",
    # Deprecated since 1.5, served until 2.0
    "/api/v1/slices",
    "/api/v1/slices/{slug}",
    "/api/v1/slices/{slug}/validate",
    "/api/v1/slices/{slug}/plan",
    "/api/v1/instances",
    "/api/v1/instances/{instance_id}",
    "/api/v1/instances/{instance_id}/actions",
    "/api/v1/jobs",
    "/api/v1/jobs/{job_id}",
    "/api/v1/jobs/{job_id}/log",
    "/api/v1/jobs/{job_id}/cancel",
    "/api/v1/jobs/{job_id}/retry",
    "/api/v1/images",
    "/api/v1/images/{name}",
    "/api/v1/roles",
    "/api/v1/ca/bundle",
    "/api/v1/map",
    # The control plane's (control/api.py), at the server's root
    "/api/v1/deployments",
    "/api/v1/deployments/{deployment_id}",
    "/api/v1/deployments/{deployment_id}/restart",
    "/api/v1/deployments/{deployment_id}/members",
    # Since 1.7 (control/accounts.py)
    "/api/v1/auth/providers",
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/password",
    "/api/v1/auth/sessions",
    "/api/v1/auth/sessions/{session_id}",
    "/api/v1/me",
    "/api/v1/users",
    "/api/v1/users/{username}",
    "/api/v1/users/{username}/reset-password",
    "/api/v1/audit",
    "/api/v1/nodes",
    "/api/v1/directory",
    # Since 1.9 (control/platform_api.py)
    "/api/v1/platform",
    "/api/v1/platform/defaults",
    "/api/v1/platform/proxmox",
    "/api/v1/platform/proxmox/{connection_id}",
    "/api/v1/platform/proxmox/{connection_id}/test",
    "/api/v1/platform/proxmox/{connection_id}/discover",
    "/api/v1/platform/crucible",
    "/api/v1/platform/crucible/{connection_id}",
    "/api/v1/platform/crucible/{connection_id}/test",
}


class ContractSpec(unittest.TestCase):
    def test_committed_files_are_current(self):
        for path, text in contract.OUTPUTS.items():
            with self.subTest(path=path.name):
                self.assertTrue(path.exists(), f"{path} is missing; run: cd grayspace-web/backend && python contract.py")
                self.assertTrue(path.read_text() == text(), f"{path} is out of date; run: cd grayspace-web/backend && python contract.py")

    def test_contract_is_only_the_terrain_api(self):
        self.assertEqual(set(contract.contract_app().openapi()["paths"]), PATHS)

    def test_full_app_serves_the_contract(self):
        """A deployment's worker (main.py) serves all but the deployments routes; the control plane those."""
        import main
        from control.main import app as control_app

        served = set(main.app.openapi()["paths"]) | set(control_app.openapi()["paths"])
        self.assertLessEqual(set(contract.contract_app().openapi()["paths"]), served)
        self.assertIn("/api/v1/events", {getattr(r, "path", None) for r in main.app.routes})
        self.assertIn("/api/v1/deployments/events", {getattr(r, "path", None) for r in control_app.routes})

    def test_routes_are_a_deployments_except_the_control_planes(self):
        spec = contract.contract_app().openapi()
        self.assertEqual(spec["servers"][0]["url"], "/deployments/{deployment}")
        for path, item in spec["paths"].items():
            with self.subTest(path=path):
                if path.startswith(contract.ROOT_PREFIXES):
                    self.assertEqual(item["servers"], [{"url": "/", "description": "The GraySpace server"}])
                else:
                    self.assertNotIn("servers", item)

    def test_every_route_needs_the_token(self):
        """All but signing in: which providers there are, and the login itself."""
        spec = contract.contract_app().openapi()
        self.assertIn("GrayspaceToken", spec["components"]["securitySchemes"])
        for path, operations in spec["paths"].items():
            for method, op in operations.items():
                if method == "servers" or path in ("/api/v1/auth/providers", "/api/v1/auth/login"):
                    continue
                with self.subTest(path=path, method=method):
                    self.assertEqual(op.get("security"), [{"GrayspaceToken": []}])

    def test_spec_is_valid_openapi(self):
        try:
            from openapi_spec_validator import validate
        except ImportError:
            self.skipTest("openapi-spec-validator is not installed (requirements-dev.txt)")
        validate(json.loads(contract.spec_text()))

    def test_schemas_are_valid_json_schema(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed (requirements-dev.txt)")
        for text in (contract.slice_schema_text(), contract.events_schema_text()):
            Draft202012Validator.check_schema(json.loads(text))



class EnclaveNames(unittest.TestCase):
    """Contract 1.5: enclave names beside the deprecated slice ones."""

    def setUp(self):
        self.spec = contract.contract_app().openapi()

    def test_slices_paths_are_deprecated_and_enclaves_are_not(self):
        for path, ops in self.spec["paths"].items():
            for op in ops.values():
                if path.startswith("/api/v1/slices"):
                    self.assertTrue(op.get("deprecated"), path)
                if path.startswith("/api/v1/enclaves"):
                    self.assertFalse(op.get("deprecated", False), path)

    def test_every_slices_route_has_an_enclaves_twin(self):
        slices = {p.removeprefix("/api/v1/slices") for p in self.spec["paths"] if p.startswith("/api/v1/slices")}
        enclaves = {p.removeprefix("/api/v1/enclaves") for p in self.spec["paths"] if p.startswith("/api/v1/enclaves")}
        self.assertEqual(slices, enclaves)

    def test_enclave_fields_sit_beside_the_slice_ones(self):
        schemas = self.spec["components"]["schemas"]
        for model, new, old in (("Capabilities", "enclave_features", "slice_features"), ("Capabilities", "enclave_pool", "slice_pool"),
                                ("InstanceSummary", "enclave", "slice"), ("JobRecord", "enclave", "slice")):
            props = (schemas.get(model) or schemas.get(f"{model}-Output"))["properties"]
            self.assertIn(new, props, f"{model}.{new}")
            self.assertTrue(props[old].get("deprecated"), f"{model}.{old}")


if __name__ == "__main__":
    unittest.main()
