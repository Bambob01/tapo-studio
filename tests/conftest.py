"""Shared test helpers: an isolated settings folder and a tiny ASGI client."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

PORT = 8765
HOST = f"127.0.0.1:{PORT}"
TOKEN = "t" * 43


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point the settings file at a temporary folder so tests never touch real data."""
    from tapo_studio import store
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(store, "FILE", tmp_path / "settings.json")
    return tmp_path


@pytest.fixture
def make_core(isolated_store):
    """Factory for a started mock-backend Core. Use inside an async test body."""
    from tapo_studio.core import Core

    async def factory():
        core = Core(mock=True)
        await core.start()
        for _ in range(100):       # wait for the mock lights to "connect"
            if core.strips and all(s.connected for s in core.strips.values()):
                break
            await asyncio.sleep(0.05)
        return core

    return factory


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body)


async def asgi(app, method, path, *, headers=None, body=b"", host=HOST, length=True, token=TOKEN, json_body=None):
    """Call an ASGI app directly (no sockets, no extra dependencies)."""
    hdrs = {"host": host}
    if token is not None and path.startswith("/api/"):
        hdrs["x-studio-token"] = token
    if json_body is not None:
        body = json.dumps(json_body).encode()
        hdrs["content-type"] = "application/json"
    for k, v in (headers or {}).items():
        hdrs[k.lower()] = v
    if method == "POST" and length:
        hdrs.setdefault("content-length", str(len(body)))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(k.encode(), str(v).encode("utf-8", "replace")) for k, v in hdrs.items()],
             "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", PORT)}
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    out = []

    async def send(msg):
        out.append(msg)

    await app(scope, receive, send)
    start = next(m for m in out if m["type"] == "http.response.start")
    return Response(start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]},
                    b"".join(m.get("body", b"") for m in out if m["type"] == "http.response.body"))
