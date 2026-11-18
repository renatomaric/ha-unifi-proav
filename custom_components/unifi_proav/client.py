"""Minimal client for the UniFi OS Pro AV (EAV-Bridge) video matrix API.

This is the internal endpoint the UniFi Network app uses, not a documented API.
It needs a local UniFi OS account: a login cookie plus a CSRF header on writes.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import aiohttp

from .planner import Op


class ProAVError(Exception):
    """Anything that went wrong talking to the controller."""


class ProAVAuthError(ProAVError):
    """Login was refused. ``code`` is the controller's reason, such as a locked account."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ProAVClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        site: str = "default",
    ) -> None:
        self._session = session
        self._base = host.rstrip("/") if host.startswith("http") else f"https://{host}"
        self._username = username
        self._password = password
        self._site = site
        self._csrf: str | None = None
        # Kept here instead of in the session's cookie jar: aiohttp's default jar drops cookies
        # from IP hosts such as 192.168.0.1, which made every request after login fail.
        self._token: str | None = None
        self._logged_in = False

    @property
    def _video(self) -> str:
        return f"{self._base}/proxy/network/v2/api/site/{self._site}/proav/video"

    async def login(self) -> None:
        """Log in once. A refused login is never retried: repeated bad logins lock the account."""
        self._logged_in = False
        try:
            async with self._session.post(
                f"{self._base}/api/auth/login",
                json={"username": self._username, "password": self._password},
            ) as resp:
                if resp.status in (401, 403):
                    code = await _error_code(resp)
                    raise ProAVAuthError(f"Login refused ({resp.status})", code)
                if resp.status >= 400:
                    raise ProAVError(f"Login failed ({resp.status})")
                cookie = resp.cookies.get("TOKEN")
                self._token = cookie.value if cookie else None
                if self._token is None:
                    raise ProAVError("Login succeeded but no session token was returned")
                self._csrf = (
                    resp.headers.get("X-CSRF-Token")
                    or resp.headers.get("X-Updated-CSRF-Token")
                    or self._csrf_from_token()
                )
        except aiohttp.ClientError as err:
            raise ProAVError(f"Cannot reach the controller: {err}") from err
        self._logged_in = True

    def _csrf_from_token(self) -> str | None:
        if self._token is None:
            return None
        try:
            payload = self._token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload)).get("csrfToken")
        except (IndexError, ValueError):
            return None

    async def _request(self, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
        if not self._logged_in:
            await self.login()
        for attempt in (1, 2):
            headers = {"Cookie": f"TOKEN={self._token}"}
            if method != "GET" and self._csrf:
                headers["X-CSRF-Token"] = self._csrf
            try:
                async with self._session.request(method, url, json=body, headers=headers) as resp:
                    if resp.status in (401, 403) and attempt == 1:
                        # Session or CSRF token expired. Log in again once, then retry once.
                        await self.login()
                        continue
                    if resp.status in (401, 403):
                        raise ProAVAuthError(f"Request refused ({resp.status})")
                    if resp.status >= 400:
                        raise ProAVError(f"{method} {url} failed ({resp.status}): {await resp.text()}")
                    if updated := resp.headers.get("X-Updated-CSRF-Token"):
                        self._csrf = updated
                    if resp.content_type == "application/json":
                        return await resp.json()
                    return None
            except aiohttp.ClientError as err:
                raise ProAVError(f"Cannot reach the controller: {err}") from err
        raise ProAVError("unreachable")

    async def get_matrix(self) -> dict[str, Any]:
        return await self._request("GET", f"{self._video}/matrix")

    async def get_names(self) -> dict[str, str]:
        """MAC to device name, as set in the UniFi Network app."""
        data = await self._request(
            "GET", f"{self._base}/proxy/network/api/s/{self._site}/stat/device"
        )
        return {d["mac"].lower(): d.get("name") or d["mac"] for d in data.get("data", [])}

    async def apply(self, ops: list[Op]) -> None:
        for op in ops:
            if op.method == "POST":
                await self._request("POST", f"{self._video}/group", op.body)
            elif op.method == "PUT":
                await self._request("PUT", f"{self._video}/group/{op.group_id}", op.body)
            elif op.method == "DELETE":
                await self._request("DELETE", f"{self._video}/group/{op.group_id}")
            else:
                raise ProAVError(f"Unknown operation {op.method}")


async def _error_code(resp: aiohttp.ClientResponse) -> str | None:
    try:
        return (await resp.json()).get("code")
    except (aiohttp.ContentTypeError, ValueError, AttributeError):
        return None
