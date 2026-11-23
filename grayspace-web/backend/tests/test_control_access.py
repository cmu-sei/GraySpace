"""Sign-in, local accounts and who may do what (control/users.py, auth.py, access.py, accounts.py, proxy.py).

The deployments' workers are fakes on their sockets that echo what reached them, so the tests see
what the proxy sends on: the service token in place of the caller's, and the caller's name.
"""

import fcntl
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from control import access, api, auth, registry, users
from control.models import CreateDeployment
from control.settings import settings
from control.supervisor import supervisor
from tests import AUTH
from tests.test_control_registry import control_isolate

PASSWORD = "correct horse battery"


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def fake_worker(test, deployment_id: str):
    """A worker that answers every request with what it received."""
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def echo(request: Request, path: str):
        return {"method": request.method, "path": "/" + path, "query": request.url.query,
                "authorization": request.headers.get("authorization"), "user": request.headers.get("x-grayspace-user")}

    @app.websocket("/{path:path}")
    async def ws(websocket: WebSocket, path: str):
        await websocket.accept()
        await websocket.send_json({"path": "/" + path, "query": websocket.url.query, "authorization": websocket.headers.get("authorization"),
                                   "user": websocket.headers.get("x-grayspace-user")})
        await websocket.close()

    server = uvicorn.Server(uvicorn.Config(app, uds=str(registry.socket_path(deployment_id)), log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    test.addCleanup(thread.join, 5)
    test.addCleanup(setattr, server, "should_exit", True)
    assert wait_for(lambda: server.started)


class Base(unittest.TestCase):
    def setUp(self):
        control_isolate(self)
        auth.throttle.clear()
        self.addCleanup(auth.throttle.clear)
        for p in (patch.object(supervisor, "wait_ready", AsyncMock(return_value=True)), patch.object(supervisor, "start_all"),
                  patch.object(supervisor, "start"), patch.object(settings, "bootstrap_admin", None), patch.object(settings, "seed_users", "")):
            p.start()
            self.addCleanup(p.stop)
        from control.main import app

        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        for name, role in (("alice", "admin"), ("bob", "builder"), ("carol", "builder"), ("uma", "user")):
            users.create(name, role, password=PASSWORD)

    def login(self, username, password=PASSWORD):
        r = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": f"Bearer {r.json()['token']}"}


class SignIn(Base):
    def test_a_session_signs_in_and_out(self):
        headers = self.login("bob")
        me = self.client.get("/api/v1/me", headers=headers).json()
        self.assertEqual((me["user"]["username"], me["role"], me["kind"]), ("bob", "builder", "user"))
        self.assertEqual(me["permissions"], ["deployments:create"])
        self.assertNotIn("password", me["user"])
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers=headers).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/me", headers=headers).status_code, 401)

    def test_the_service_token_is_an_admin(self):
        me = self.client.get("/api/v1/me", headers=AUTH).json()
        self.assertEqual((me["kind"], me["role"], me["user"]), ("service", "admin", None))

    def test_a_wrong_password_and_an_unknown_user_look_the_same(self):
        wrong = self.client.post("/api/v1/auth/login", json={"username": "bob", "password": "nope nope nope"})
        unknown = self.client.post("/api/v1/auth/login", json={"username": "nobody", "password": "nope nope nope"})
        self.assertEqual((wrong.status_code, wrong.json()), (unknown.status_code, unknown.json()))
        self.assertEqual(wrong.status_code, 401)

    def test_repeated_failures_are_throttled(self):
        for _ in range(auth.Throttle.FREE):
            self.client.post("/api/v1/auth/login", json={"username": "bob", "password": "wrong password!"})
        r = self.client.post("/api/v1/auth/login", json={"username": "bob", "password": PASSWORD})
        self.assertEqual(r.status_code, 429)
        self.assertGreaterEqual(int(r.headers["retry-after"]), 1)

    def test_a_disabled_user_cant_sign_in_and_their_sessions_end(self):
        headers = self.login("uma")
        self.client.patch("/api/v1/users/uma", json={"disabled": True}, headers=self.login("alice"))
        self.assertEqual(self.client.get("/api/v1/me", headers=headers).status_code, 401)
        r = self.client.post("/api/v1/auth/login", json={"username": "uma", "password": PASSWORD})
        self.assertEqual(r.status_code, 401)
        self.assertIn("disabled", r.json()["detail"])

    def test_a_one_time_password_must_be_changed_first(self):
        r = self.client.post("/api/v1/users", json={"username": "dan", "role": "user"}, headers=AUTH)
        self.assertEqual(r.status_code, 201, r.text)
        one_time = r.json()["one_time_password"]
        login = self.client.post("/api/v1/auth/login", json={"username": "dan", "password": one_time}).json()
        self.assertTrue(login["must_change_password"])
        headers = {"Authorization": f"Bearer {login['token']}"}
        self.assertTrue(self.client.get("/api/v1/me", headers=headers).json()["must_change_password"])
        self.assertEqual(self.client.get("/api/v1/deployments", headers=headers).status_code, 403)
        short = self.client.post("/api/v1/auth/password", json={"current_password": one_time, "new_password": "short"}, headers=headers)
        self.assertEqual(short.status_code, 422)
        r = self.client.post("/api/v1/auth/password", json={"current_password": one_time, "new_password": PASSWORD}, headers=headers)
        self.assertEqual(r.status_code, 204, r.text)
        self.assertEqual(self.client.get("/api/v1/deployments", headers=headers).status_code, 200)

    def test_changing_the_password_ends_the_other_sessions(self):
        first, second = self.login("bob"), self.login("bob")
        self.assertEqual(len(self.client.get("/api/v1/auth/sessions", headers=first).json()), 2)
        r = self.client.post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": PASSWORD + "!"}, headers=first)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/me", headers=second).status_code, 401)
        sessions = self.client.get("/api/v1/auth/sessions", headers=first).json()
        self.assertEqual([s["current"] for s in sessions], [True])

    def test_session_tokens_arent_stored(self):
        headers = self.login("bob")
        token = headers["Authorization"].split()[1]
        self.assertNotIn(token, (settings.root / "sessions.json").read_text())
        self.assertNotIn(PASSWORD, (settings.root / "users.json").read_text())

    def test_behind_nginx_the_address_is_the_one_it_passes_on(self):
        nginx = {"client": None, "headers": [(b"x-real-ip", b"10.1.2.3")]}
        self.assertEqual(auth.scope_ip(nginx), "10.1.2.3")
        self.assertEqual(auth.scope_ip({"client": ("127.0.0.1", 5), "headers": [(b"x-forwarded-for", b"10.9.9.9, 1.1.1.1")]}), "10.9.9.9")
        # A remote caller can't claim another address
        self.assertEqual(auth.scope_ip({"client": ("192.0.2.7", 5), "headers": [(b"x-real-ip", b"10.1.2.3")]}), "192.0.2.7")
        self.assertEqual(auth.scope_ip({"client": None, "headers": []}), "")

    def test_a_sign_in_without_a_client_address_works(self):
        # The container's uvicorn, behind nginx on a socket, has no client address
        with patch.object(auth, "client_ip", return_value=""):
            r = self.client.post("/api/v1/auth/login", json={"username": "bob", "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)

    def test_providers_need_no_token(self):
        self.assertEqual(self.client.get("/api/v1/auth/providers").json(), {"providers": ["local"]})


class Users(Base):
    def test_only_admins_manage_users(self):
        self.assertEqual(self.client.get("/api/v1/users", headers=self.login("bob")).status_code, 403)
        names = [u["username"] for u in self.client.get("/api/v1/users", headers=self.login("alice")).json()]
        self.assertEqual(names, ["alice", "bob", "carol", "uma"])

    def test_owners_see_who_they_can_add(self):
        users.update("carol", disabled=True)
        r = self.client.get("/api/v1/directory", headers=self.login("bob"))
        self.assertEqual(r.json(), [{"username": "alice", "name": "alice", "role": "admin"}, {"username": "bob", "name": "bob", "role": "builder"},
                                    {"username": "uma", "name": "uma", "role": "user"}])
        self.assertEqual(self.client.get("/api/v1/directory", headers=self.login("uma")).status_code, 403)

    def test_a_reset_gives_a_one_time_password_and_ends_sessions(self):
        headers = self.login("uma")
        r = self.client.post("/api/v1/users/uma/reset-password", headers=self.login("alice"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["user"]["must_change_password"])
        self.assertEqual(self.client.get("/api/v1/me", headers=headers).status_code, 401)
        login = self.client.post("/api/v1/auth/login", json={"username": "uma", "password": r.json()["one_time_password"]})
        self.assertTrue(login.json()["must_change_password"])

    def test_admins_cant_lock_themselves_out(self):
        headers = self.login("alice")
        self.assertEqual(self.client.patch("/api/v1/users/alice", json={"role": "user"}, headers=headers).status_code, 409)
        self.assertEqual(self.client.delete("/api/v1/users/alice", headers=headers).status_code, 409)

    def test_a_new_user_can_join_deployments(self):
        registry.create(CreateDeployment(id="d1"), owner="bob")
        r = self.client.post("/api/v1/users", json={"username": "eve", "deployments": ["d1"]}, headers=AUTH)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(registry.get("d1")["members"], [{"user": "eve", "access": "use"}])

    def test_deleting_a_user_takes_them_off_deployments(self):
        registry.create(CreateDeployment(id="d1"), owner="bob")
        registry.set_members("d1", [{"user": "uma", "access": "use"}])
        self.assertEqual(self.client.delete("/api/v1/users/uma", headers=AUTH).status_code, 204)
        self.assertEqual(registry.get("d1")["members"], [])


class Access(Base):
    def setUp(self):
        super().setUp()
        registry.create(CreateDeployment(id="d1"), owner="bob")
        registry.set_members("d1", [{"user": "uma", "access": "use"}, {"user": "carol", "access": "build"}])
        registry.create(CreateDeployment(id="d2"), owner="alice")
        for i in ("d1", "d2"):
            fake_worker(self, i)

    def ids(self, headers):
        r = self.client.get("/api/v1/deployments", headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        return {d["id"]: d["my_access"] for d in r.json()}

    def test_the_list_is_whats_yours(self):
        self.assertEqual(set(self.ids(self.login("alice"))), {"d1", "d2"})
        self.assertEqual(self.ids(self.login("bob")), {"d1": access.DEPLOYMENT_PERMISSIONS})
        self.assertEqual(self.ids(self.login("carol")), {"d1": ["read", "operate", "build"]})
        self.assertEqual(self.ids(self.login("uma")), {"d1": ["read", "operate"]})

    def test_others_deployments_are_not_found(self):
        uma = self.login("uma")
        self.assertEqual(self.client.get("/api/v1/deployments/d2", headers=uma).status_code, 404)
        self.assertEqual(self.client.get("/deployments/d2/api/v1/core", headers=uma).status_code, 404)

    def test_the_worker_gets_the_service_token_and_the_caller(self):
        uma = self.login("uma")
        r = self.client.get("/deployments/d1/api/v1/topology/routers?x=1", headers={**uma, "X-Grayspace-User": "alice"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"method": "GET", "path": "/api/v1/topology/routers", "query": "x=1",
                                    "authorization": "Bearer test-token", "user": "uma"})

    def test_a_user_acts_but_doesnt_build(self):
        uma = self.login("uma")
        self.assertEqual(self.client.post("/deployments/d1/api/v1/instances/bk/actions", json={}, headers=uma).status_code, 200)
        self.assertEqual(self.client.post("/deployments/d1/api/v1/scenarios/discard-changes", headers=uma).status_code, 200)
        self.assertEqual(self.client.post("/deployments/d1/api/v1/changes/discard", headers=uma).status_code, 200)
        self.assertEqual(self.client.post("/deployments/d1/api/v1/core/presets/slow-links/apply", headers=uma).status_code, 200)
        for method, path in (("POST", "base/configure"), ("POST", "base/deploy"), ("POST", "instances"), ("PUT", "enclaves/bank"),
                             ("POST", "base/presets"), ("DELETE", "base/presets/slow-links"), ("POST", "jobs/j1/retry"), ("DELETE", "base")):
            r = self.client.request(method, f"/deployments/d1/api/v1/{path}", json={}, headers=uma)
            self.assertEqual(r.status_code, 403, f"{method} {path}: {r.text}")

    def test_build_access_builds_but_doesnt_destroy_the_base(self):
        carol = self.login("carol")
        self.assertEqual(self.client.post("/deployments/d1/api/v1/core/configure", headers=carol).status_code, 200)
        self.assertEqual(self.client.delete("/deployments/d1/api/v1/core", headers=carol).status_code, 403)
        self.assertEqual(self.client.delete("/deployments/d1/api/v1/core", headers=self.login("bob")).status_code, 200)

    def test_the_default_alias_is_checked_too(self):
        registry.set_default("d2")
        self.assertEqual(self.client.get("/api/v1/core", headers=self.login("uma")).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/core", headers=self.login("alice")).status_code, 200)

    def test_websockets_are_checked_and_get_the_service_token(self):
        uma = self.login("uma")["Authorization"].split()[1]
        with self.client.websocket_connect(f"/deployments/d1/ws/console/r1?cols=80&token={uma}") as ws:
            seen = ws.receive_json()
        self.assertEqual((seen["path"], seen["query"], seen["authorization"], seen["user"]),
                         ("/ws/console/r1", "cols=80", "Bearer test-token", "uma"))
        for path in (f"/deployments/d2/ws/console/r1?token={uma}", "/deployments/d1/ws/console/r1"):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect(path) as ws:
                    ws.receive_json()

    def test_members_rules(self):
        bob = self.login("bob")
        put = lambda members, h=bob: self.client.put("/api/v1/deployments/d1/members", json={"members": members}, headers=h)
        self.assertEqual(put([{"user": "uma", "access": "build"}]).status_code, 422)
        self.assertEqual(put([{"user": "ghost", "access": "use"}]).status_code, 422)
        self.assertEqual(put([{"user": "bob", "access": "use"}]).status_code, 422)
        self.assertEqual(put([{"user": "uma", "access": "use"}], self.login("carol")).status_code, 403)
        r = put([{"user": "carol", "access": "use"}])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["members"], [{"user": "carol", "access": "use"}])
        self.assertEqual(self.ids(self.login("uma")), {})

    def test_who_creates_and_changes_deployments(self):
        self.assertEqual(self.client.post("/api/v1/deployments", json={"id": "d3"}, headers=self.login("uma")).status_code, 403)
        bob = self.login("bob")
        r = self.client.post("/api/v1/deployments", json={"id": "d3"}, headers=bob)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["owner"], "bob")
        self.assertEqual(self.client.patch("/api/v1/deployments/d3", json={"default": True}, headers=bob).status_code, 403)
        self.assertEqual(self.client.patch("/api/v1/deployments/d3", json={"name": "Mine"}, headers=bob).status_code, 200)
        self.assertEqual(self.client.patch("/api/v1/deployments/d1", json={"name": "No"}, headers=self.login("carol")).status_code, 403)

    def test_events_go_to_those_who_can_see_the_deployment(self):
        import asyncio

        queues = {name: asyncio.Queue() for name in ("alice", "bob", "uma")}
        for name, queue in queues.items():
            record = users.get(name)
            api._subscribers[queue] = access.Principal(username=name, role=record["role"])
        self.addCleanup(api._subscribers.clear)
        api.publish("deployment.updated", "d2")
        api.publish("deployment.updated", "d1")
        got = {name: [q.get_nowait()["id"] for _ in range(q.qsize())] for name, q in queues.items()}
        self.assertEqual(got, {"alice": ["d2", "d1"], "bob": ["d1"], "uma": ["d1"]})


class RouteTable(unittest.TestCase):
    def test_every_worker_route_that_isnt_a_read_is_listed(self):
        from main import app

        missing = []
        for route in app.routes:
            path = route.path.replace("{name:path}", "x")
            sample = "".join("x" if part.startswith("{") else part for part in _split(path))
            methods = ["WS"] if isinstance(route, WebSocketRoute) else sorted(set(route.methods or []) - access.READ_METHODS)
            for method in methods:
                if access.listed_class(method, sample) is None:
                    missing.append(f"{method} {route.path}")
        self.assertEqual(missing, [], "Add these to control/access.py WORKER_ROUTES")

    def test_consoles_need_operate_and_reads_read(self):
        self.assertEqual(access.route_class("WS", "ws/console/r1"), "operate")
        self.assertEqual(access.route_class("GET", "api/v1/anything/new"), "read")
        self.assertEqual(access.route_class("POST", "api/v1/anything/new"), "build")
        self.assertEqual(access.route_class("WS", "ws/anything"), "operate")


def _split(path):
    import re

    return re.split(r"(\{[^}]+\})", path)


class Audit(Base):
    def test_writes_and_sign_ins_are_recorded(self):
        registry.create(CreateDeployment(id="d1"), owner="bob")
        fake_worker(self, "d1")
        bob = self.login("bob")
        self.client.post("/api/v1/auth/login", json={"username": "bob", "password": "wrong password!"})
        self.client.post("/deployments/d1/api/v1/instances", json={}, headers=bob)
        self.client.get("/deployments/d1/api/v1/core", headers=bob)
        self.assertEqual(self.client.get("/api/v1/audit", headers=bob).status_code, 403)
        entries = self.client.get("/api/v1/audit", headers=self.login("alice")).json()
        summary = [(e["user"], e["method"], e["path"], e["status"], e.get("deployment")) for e in entries]
        self.assertIn(("bob", "POST", "/deployments/d1/api/v1/instances", 200, "d1"), summary)
        self.assertIn(("bob", "POST", "/api/v1/auth/login", 401, None), summary)
        self.assertIn(("bob", "POST", "/api/v1/auth/login", 200, None), summary)
        self.assertFalse(any(e["method"] == "GET" for e in entries))
        only = self.client.get("/api/v1/audit?deployment=d1", headers=AUTH).json()
        self.assertEqual({e["deployment"] for e in only}, {"d1"})
        self.assertNotIn(PASSWORD, (settings.root / "audit.jsonl").read_text())


class Bootstrap(unittest.TestCase):
    def setUp(self):
        control_isolate(self)

    def test_the_bootstrap_admin_gets_a_one_time_password_once(self):
        logged = []
        with patch.object(settings, "bootstrap_admin", "root"):
            users.prepare(logged.append)
            users.prepare(logged.append)
        self.assertEqual(len(logged), 1)
        one_time = logged[0].rsplit(" ", 1)[1]
        record = users.authenticate("root", one_time)
        self.assertEqual((record["role"], record["must_change_password"]), ("admin", True))

    def test_seed_users(self):
        with patch.object(settings, "seed_users", "admin:admin,builder:builder,user"), patch.object(settings, "seed_password", PASSWORD):
            users.prepare(lambda _: None)
        self.assertEqual([(u["username"], u["role"], u["must_change_password"]) for u in users.all_users()],
                         [("admin", "admin", False), ("builder", "builder", False), ("user", "user", False)])

    def test_deployments_without_an_owner_get_one(self):
        registry.create(CreateDeployment(id="old"))
        registry.create(CreateDeployment(id="new"), owner="bob")
        self.assertEqual(registry.adopt_owners("root"), ["old"])
        self.assertEqual((registry.get("old")["owner"], registry.get("new")["owner"]), ("root", "bob"))

    def test_the_command_line_creates_an_account(self):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(users.main(["create", "ops", "--role", "builder"]), 0)
        self.assertEqual(users.get("ops")["role"], "builder")
        self.assertIn("One-time password", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(users.main(["create", "ops"]), 1)


class Nodes(Base):
    def setUp(self):
        super().setUp()
        p = patch("control.probes.capacity", AsyncMock(return_value=None))
        p.start()
        self.addCleanup(p.stop)

    def test_nodes_group_deployments_and_show_the_lock(self):
        registry.create(CreateDeployment(id="d1"), owner="bob")
        registry.create(CreateDeployment(id="d2"), owner="bob")
        self.assertEqual(self.client.get("/api/v1/nodes", headers=self.login("bob")).status_code, 403)
        nodes = self.client.get("/api/v1/nodes", headers=AUTH).json()
        self.assertEqual([(n["host"], n["node"], n["deployments"], n["locked"]) for n in nodes], [("pve-n1.example", "pve", ["d1", "d2"], False)])
        with open(registry.lock_file("pve-n1.example"), "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            self.assertTrue(self.client.get("/api/v1/nodes", headers=AUTH).json()[0]["locked"])


class Passwords(unittest.TestCase):
    def test_hashes_verify_and_differ(self):
        a, b = users.hash_password(PASSWORD), users.hash_password(PASSWORD)
        self.assertNotEqual(a, b)
        self.assertTrue(users.verify_password(PASSWORD, a))
        self.assertFalse(users.verify_password(PASSWORD + "x", a))
        self.assertFalse(users.verify_password(PASSWORD, "garbage"))


class WorkerSummary(unittest.TestCase):
    def test_nothing_deployed(self):
        from main import app

        r = TestClient(app).get("/api/v1/summary", headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["base"]["status"], r.json()["instances_live"], r.json()["active_job"]), ("not_deployed", 0, None))


if __name__ == "__main__":
    unittest.main()
