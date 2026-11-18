"""Tests for the API client against a fake UniFi OS controller."""

import base64
import importlib
import json
import sys
import types
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

# Load the integration modules without running its __init__ (which needs Home Assistant).
package_dir = Path(__file__).parents[1] / "custom_components/unifi_proav"
core = types.ModuleType("unifi_proav_core")
core.__path__ = [str(package_dir)]
sys.modules["unifi_proav_core"] = core
client_module = importlib.import_module("unifi_proav_core.client")
planner = importlib.import_module("unifi_proav_core.planner")

ProAVClient = client_module.ProAVClient
ProAVAuthError = client_module.ProAVAuthError
ProAVError = client_module.ProAVError
Op = planner.Op

CSRF = "csrf-token-1"
VIDEO = "/proxy/network/v2/api/site/default/proav/video"


def jwt_with_csrf(token: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"csrfToken": token}).encode()).decode().rstrip("=")
    return f"header.{payload}.sig"


class FakeController:
    def __init__(self) -> None:
        self.login_attempts = 0
        self.locked = False
        self.send_csrf_header = True
        self.expire_next = 0  # answer this many authenticated requests with 401
        self.always_401 = False
        self.calls: list[tuple[str, str, dict | None, str | None]] = []
        self.matrix = {"bridges": [], "hosts": [], "clients": [], "groups": []}

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/auth/login", self.login)
        app.router.add_get(f"{VIDEO}/matrix", self.get_matrix)
        app.router.add_post(f"{VIDEO}/group", self.write)
        app.router.add_put(f"{VIDEO}/group/{{gid}}", self.write)
        app.router.add_delete(f"{VIDEO}/group/{{gid}}", self.write)
        app.router.add_get("/proxy/network/api/s/default/stat/device", self.devices)
        return app

    async def login(self, request: web.Request) -> web.Response:
        self.login_attempts += 1
        body = await request.json()
        if self.locked:
            return web.json_response(
                {"message": "SSO Account locked", "code": "AUTHENTICATION_FAILED_ACCOUNT_LOCKED"},
                status=403,
            )
        assert body == {"username": "ha", "password": "secret"}
        headers = {"X-CSRF-Token": CSRF} if self.send_csrf_header else {}
        response = web.json_response({"ok": True}, headers=headers)
        response.set_cookie("TOKEN", jwt_with_csrf(CSRF))
        return response

    def _authorized(self, request: web.Request) -> bool:
        if self.always_401:
            return False
        if self.expire_next:
            self.expire_next -= 1
            return False
        return "TOKEN" in request.cookies

    async def get_matrix(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.Response(status=401)
        self.calls.append(("GET", request.path, None, None))
        return web.json_response(self.matrix)

    async def write(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.Response(status=401)
        body = await request.json() if request.can_read_body else None
        self.calls.append((request.method, request.path, body, request.headers.get("X-CSRF-Token")))
        if request.headers.get("X-CSRF-Token") != CSRF:
            return web.Response(status=403)
        return web.json_response({"id": "new"}, status=201 if request.method == "POST" else 200)

    async def devices(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.Response(status=401)
        return web.json_response(
            {"data": [{"mac": "AA:00:00:00:00:01", "name": "Ormar HT"}, {"mac": "aa:00:00:00:00:02"}]}
        )


@pytest.fixture
async def controller():
    fake = FakeController()
    server = TestServer(fake.app())
    await server.start_server()
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    fake.client = ProAVClient(session, str(server.make_url("")), "ha", "secret")
    yield fake
    await session.close()
    await server.close()


async def test_reads_the_matrix_after_logging_in(controller):
    controller.matrix["hosts"] = ["aa:00:00:00:00:01"]

    matrix = await controller.client.get_matrix()

    assert matrix["hosts"] == ["aa:00:00:00:00:01"]
    assert controller.login_attempts == 1


async def test_writes_carry_the_csrf_token_from_login(controller):
    await controller.client.apply([Op("PUT", "g1", {"name": "x"}), Op("DELETE", "g2", None)])

    assert controller.calls == [
        ("PUT", f"{VIDEO}/group/g1", {"name": "x"}, CSRF),
        ("DELETE", f"{VIDEO}/group/g2", None, CSRF),
    ]


async def test_csrf_falls_back_to_the_login_cookie(controller):
    controller.send_csrf_header = False

    await controller.client.apply([Op("POST", None, {"name": "new"})])

    assert controller.calls == [("POST", f"{VIDEO}/group", {"name": "new"}, CSRF)]


async def test_operations_use_the_right_paths(controller):
    await controller.client.apply(
        [Op("DELETE", "a", None), Op("PUT", "b", {"k": 1}), Op("POST", None, {"k": 2})]
    )

    assert [(m, p) for m, p, _, _ in controller.calls] == [
        ("DELETE", f"{VIDEO}/group/a"),
        ("PUT", f"{VIDEO}/group/b"),
        ("POST", f"{VIDEO}/group"),
    ]


async def test_a_locked_account_is_reported_and_not_retried(controller):
    controller.locked = True

    with pytest.raises(ProAVAuthError) as err:
        await controller.client.get_matrix()

    assert err.value.code == "AUTHENTICATION_FAILED_ACCOUNT_LOCKED"
    assert controller.login_attempts == 1


async def test_an_expired_session_logs_in_again_once(controller):
    controller.expire_next = 1

    await controller.client.get_matrix()

    assert controller.login_attempts == 2


async def test_a_session_that_keeps_failing_gives_up_after_one_relogin(controller):
    controller.always_401 = True

    with pytest.raises(ProAVAuthError):
        await controller.client.get_matrix()

    assert controller.login_attempts == 2


async def test_names_map_lowercase_macs_and_fall_back_to_the_mac(controller):
    names = await controller.client.get_names()

    assert names == {"aa:00:00:00:00:01": "Ormar HT", "aa:00:00:00:00:02": "aa:00:00:00:00:02"}


async def test_an_unreachable_controller_raises_a_client_error():
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    client = ProAVClient(session, "http://127.0.0.1:1", "ha", "secret")
    try:
        with pytest.raises(ProAVError):
            await client.get_matrix()
    finally:
        await session.close()
