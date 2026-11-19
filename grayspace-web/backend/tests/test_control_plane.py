"""Two deployments behind one control plane, with real workers (control/, docs/deployments.md).

The workers simulate their deployment (DEV_SIMULATE_DEPLOYMENT): each serves its own copy of the
golden tfvars and has its own data directory, so what one stores the other doesn't see.
"""

import asyncio
import os
import signal
import threading
import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from control import registry
from control.models import CreateDeployment
from control.settings import settings
from control.supervisor import supervisor
from tests import AUTH, REPO
from tests.test_control_registry import control_isolate

SPEC = {"apiVersion": "grayspace/v1", "kind": "Enclave", "slug": "bank", "prefix": "bk",
        "sites": [{"name": "hq", "attach": {"isp": "Lumen", "location": "Chicago"}, "segments": [{"name": "lan"}]}],
        "hosts": [{"name": "ws", "segment": "lan", "image": "grayspace-ubuntu-lxc"}]}


def wait_for(predicate, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


class TwoDeployments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = control_isolate(cls, site="", cleanup=cls.addClassCleanup)
        for key, value in {"simulate": True, "simulate_tfvars": str(REPO / "tests" / "golden" / "tfvars-full.json"),
                           "bootstrap": "a,b", "source_path": str(REPO)}.items():
            p = patch.object(settings, key, value)
            p.start()
            cls.addClassCleanup(p.stop)
        from control.main import app

        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)
        ready = wait_for(lambda: all(supervisor.running(i) for i in ("a", "b")), 90)
        assert ready, {i: supervisor.status(i) for i in ("a", "b")}

    def test_the_list_shows_both_and_their_bases(self):
        r = self.client.get("/api/v1/deployments", headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        by_id = {d["id"]: d for d in r.json()}
        self.assertEqual(set(by_id), {"a", "b"})
        self.assertTrue(by_id["a"]["default"])
        self.assertEqual(by_id["b"]["url"], "/deployments/b")
        self.assertEqual(by_id["a"]["worker"]["state"], "running")
        self.assertEqual(by_id["a"]["base"]["version"]["name"], "grayspace/2026.11")

    def test_the_registry_needs_the_token(self):
        self.assertEqual(self.client.get("/api/v1/deployments").status_code, 401)

    def test_what_one_deployment_stores_the_other_doesnt_see(self):
        r = self.client.put("/deployments/a/api/v1/enclaves/bank", json=SPEC, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        slugs = lambda d: {e["slug"] for e in self.client.get(f"/deployments/{d}/api/v1/enclaves", headers=AUTH).json()}
        self.assertIn("bank", slugs("a"))
        self.assertNotIn("bank", slugs("b"))

    def test_the_unprefixed_routes_reach_the_default_and_are_deprecated(self):
        r = self.client.get("/api/v1/base", headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.headers["deprecation"], r.headers["x-grayspace-deployment"]), ("true", "a"))
        # A UI route: its GETs need no token on the worker, but every request through the control plane does
        self.assertEqual(len(self.client.get("/api/v1/topology/routers", headers=AUTH).json()), 134)
        self.assertEqual(self.client.get("/api/v1/topology/routers").status_code, 401)

    def test_every_request_needs_a_token(self):
        self.assertEqual(self.client.get("/deployments/b/api/v1/base").status_code, 401)
        self.assertEqual(self.client.get("/deployments/b/api/v1/topology/routers").status_code, 401)
        self.assertEqual(self.client.get("/deployments/zz/api/v1/base").status_code, 401)  # nothing said about which exist
        self.assertEqual(self.client.get("/deployments/zz/api/v1/base", headers=AUTH).status_code, 404)

    def test_websockets_are_relayed(self):
        with self.client.websocket_connect("/deployments/b/api/v1/events?token=test-token") as ws:
            self.assertEqual(ws.receive_json()["type"], "hello")
            ws.send_text("ping")
            self.assertEqual(ws.receive_json()["type"], "pong")
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/deployments/b/api/v1/events") as ws:
                ws.receive_json()

    def test_create_and_remove_a_deployment(self):
        r = self.client.post("/api/v1/deployments", json={"id": "c", "name": "Third", "router_password": uuid.uuid4().hex}, headers=AUTH)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["secrets"], ["router_password"])
        self.assertEqual(self.client.post("/api/v1/deployments", json={"id": "c"}, headers=AUTH).status_code, 409)
        self.assertTrue(wait_for(lambda: supervisor.running("c")))
        self.assertEqual(self.client.get("/deployments/c/api/v1/base", headers=AUTH).status_code, 200)
        self.assertEqual(self.client.delete("/api/v1/deployments/c", headers=AUTH).status_code, 204)
        self.assertIsNone(registry.get("c"))
        self.assertEqual(self.client.get("/deployments/c/api/v1/base", headers=AUTH).status_code, 404)

    def test_a_killed_worker_is_restarted(self):
        before = supervisor.status("b")
        os.kill(before.pid, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: supervisor.running("b") and supervisor.status("b").pid != before.pid))
        self.assertEqual(supervisor.status("b").restarts, before.restarts + 1)
        self.assertEqual(self.client.get("/deployments/b/api/v1/base", headers=AUTH).status_code, 200)


class Streaming(unittest.TestCase):
    """The followed job log streams through the proxy as the worker writes it."""

    def setUp(self):
        control_isolate(self)
        registry.create(CreateDeployment(id="s"))
        fake = FastAPI()

        @fake.get("/api/v1/jobs/x/log")
        async def log():
            async def lines():
                for i in range(3):
                    yield f"line {i}\n"
                    await asyncio.sleep(0.2)
            return StreamingResponse(lines(), media_type="text/plain")

        server = uvicorn.Server(uvicorn.Config(fake, uds=str(registry.socket_path("s")), log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(setattr, server, "should_exit", True)
        self.assertTrue(wait_for(lambda: server.started, 10))
        # The fake worker is the deployment's; the supervisor starts none
        for p in (patch.object(supervisor, "wait_ready", AsyncMock(return_value=True)), patch.object(supervisor, "start_all")):
            p.start()
            self.addCleanup(p.stop)

    def test_lines_arrive_before_the_job_ends(self):
        # TestClient reads the whole body before returning, so the control plane runs in uvicorn
        import socket

        import httpx

        from control.main import app

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(setattr, server, "should_exit", True)
        self.assertTrue(wait_for(lambda: server.started, 10))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/deployments/s/api/v1/jobs/x/log?follow=true"
        arrivals = []
        with httpx.stream("GET", url, headers=AUTH, timeout=10) as r:
            self.assertEqual(r.status_code, 200)
            for line in r.iter_lines():
                arrivals.append((line, time.monotonic()))
        self.assertEqual([a[0] for a in arrivals], ["line 0", "line 1", "line 2"])
        self.assertGreater(arrivals[-1][1] - arrivals[0][1], 0.3)

if __name__ == "__main__":
    unittest.main()
