"""Bearer-token auth (services/auth.py): contract routes, write routes and console websockets."""

import unittest
from unittest.mock import patch

from fastapi import APIRouter, Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from config import settings
from services import auth
from tests import AUTH


def app_under_test():
    app = FastAPI()
    contract = APIRouter(prefix="/api/v1", dependencies=[Depends(auth.require_token)])

    @contract.get("/capabilities")
    async def capabilities():
        return {"ok": True}

    ui = APIRouter(prefix="/api/v1/ui")

    @ui.get("/routers")
    async def read():
        return ["lum-chi"]

    @ui.post("/move")
    async def write():
        return {"moved": True}

    @app.websocket("/ws/console/{hostname}")
    async def console(websocket: WebSocket, hostname: str):
        await websocket.accept()
        await websocket.send_json({"hostname": hostname})
        await websocket.close()

    @app.websocket("/ws")
    async def state(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"type": "initial_link_states"})
        await websocket.close()

    app.include_router(contract)
    app.include_router(ui)
    app.add_middleware(auth.TokenAuthMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])
    return TestClient(app)


class ContractRoutes(unittest.TestCase):
    def test_token_required(self):
        client = app_under_test()
        self.assertEqual(client.get("/api/v1/capabilities").status_code, 401)
        self.assertEqual(client.get("/api/v1/capabilities", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(client.get("/api/v1/capabilities", headers=AUTH).json(), {"ok": True})

    def test_unset_token_refuses(self):
        with patch.object(settings, "api_token", None):
            response = app_under_test().get("/api/v1/capabilities", headers=AUTH)
        self.assertEqual(response.status_code, 503)


class WriteRoutes(unittest.TestCase):
    def test_reads_stay_open(self):
        self.assertEqual(app_under_test().get("/api/v1/ui/routers").status_code, 200)

    def test_writes_need_the_token(self):
        client = app_under_test()
        response = client.post("/api/v1/ui/move")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertEqual(client.post("/api/v1/ui/move", headers=AUTH).json(), {"moved": True})

    def test_unset_token_refuses_writes(self):
        with patch.object(settings, "api_token", None):
            self.assertEqual(app_under_test().post("/api/v1/ui/move", headers=AUTH).status_code, 503)


class Websockets(unittest.TestCase):
    def test_console_needs_the_token(self):
        client = app_under_test()
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/ws/console/lum-chi") as ws:
                ws.receive_json()
        with client.websocket_connect("/ws/console/lum-chi?token=test-token") as ws:
            self.assertEqual(ws.receive_json(), {"hostname": "lum-chi"})
        with client.websocket_connect("/ws/console/lum-chi", headers=AUTH) as ws:
            self.assertEqual(ws.receive_json(), {"hostname": "lum-chi"})

    def test_state_stream_stays_open(self):
        with app_under_test().websocket_connect("/ws") as ws:
            self.assertEqual(ws.receive_json()["type"], "initial_link_states")


class Cors(unittest.TestCase):
    def test_only_configured_origins(self):
        client = app_under_test()
        allowed = client.options("/api/v1/ui/move", headers={"Origin": "http://ui.example", "Access-Control-Request-Method": "POST"})
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "http://ui.example")
        other = client.options("/api/v1/ui/move", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
        self.assertNotIn("access-control-allow-origin", other.headers)

    def test_rejections_carry_cors_headers(self):
        response = app_under_test().post("/api/v1/ui/move", headers={"Origin": "http://ui.example"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://ui.example")


if __name__ == "__main__":
    unittest.main()
