"""Security regression tests for the local web server (every item here was an attack we checked)."""

import asyncio
import re

import pytest

from conftest import HOST, PORT, TOKEN, asgi
from tapo_studio.server import build_app


@pytest.fixture
def app_factory(make_core):
    async def make():
        core = await make_core()
        return core, build_app(core, PORT, TOKEN)
    return make


def run(coro):
    return asyncio.run(coro)


def test_token_is_required_and_never_in_the_page(app_factory):
    async def go():
        core, app = await app_factory()
        assert (await asgi(app, "GET", "/api/state", token=None)).status == 403
        assert (await asgi(app, "GET", "/api/state", token="wrong")).status == 403
        assert (await asgi(app, "GET", "/api/state", token="é" * 40)).status == 403      # non-ASCII must not 500
        assert (await asgi(app, "GET", "/api/state")).status == 200
        page = await asgi(app, "GET", "/")
        assert page.status == 200 and TOKEN.encode() not in page.body                     # served to nobody
    run(go())


@pytest.mark.parametrize("headers,host", [
    ({}, "evil.com"), ({}, f"evil.com:{PORT}"), ({}, "127.0.0.1.nip.io:8765"), ({}, f"localhost.:{PORT}"),
    ({}, f"[::1]:{PORT}"), ({"origin": "https://evil.com"}, HOST), ({"origin": "null"}, HOST),
    ({"sec-fetch-site": "cross-site"}, HOST), ({"sec-fetch-site": "same-site"}, HOST),
])
def test_cross_site_and_rebinding_requests_are_refused(app_factory, headers, host):
    async def go():
        _, app = await app_factory()
        assert (await asgi(app, "GET", "/api/state", headers=headers, host=host)).status == 403
        assert (await asgi(app, "GET", "/", headers=headers, host=host)).status in (403,)
    run(go())


def test_security_headers(app_factory):
    async def go():
        _, app = await app_factory()
        h = (await asgi(app, "GET", "/")).headers
        assert "default-src 'none'" in h["content-security-policy"] and "script-src 'self'" in h["content-security-policy"]
        assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
    run(go())


def test_request_shape_rules(app_factory):
    async def go():
        _, app = await app_factory()
        post = lambda **kw: asgi(app, "POST", "/api/live/stop", **kw)            # noqa: E731
        assert (await post(body=b"a=b", headers={"content-type": "application/x-www-form-urlencoded"})).status == 400
        assert (await post(body=b"{}", headers={"content-type": "text/plain"})).status == 400
        assert (await post(json_body={}, length=False)).status == 411             # chunked/no length: refused
        assert (await post(json_body={}, headers={"content-length": str(10**7)})).status == 413
        assert (await post(body=b'{"a": NaN}', headers={"content-type": "application/json"})).status == 400
        assert (await post(body=b"[1,2]", headers={"content-type": "application/json"})).status == 400
        assert (await post(body=b"[" * 100000, headers={"content-type": "application/json"})).status == 400
        assert (await asgi(app, "PUT", "/api/live/stop", json_body={})).status == 405
    run(go())


def test_static_files_cannot_escape_their_folder(app_factory):
    async def go():
        _, app = await app_factory()
        for p in ("/static/../server.py", "/static/..%2fserver.py", "/static/%2e%2e/server.py", "/static/../../README.md"):
            assert (await asgi(app, "GET", p)).status in (404, 400)
        assert (await asgi(app, "GET", "/static/app.js")).status == 200
    run(go())


def test_devices_must_be_on_the_home_network(app_factory):
    async def go():
        _, app = await app_factory()
        for ip in ("8.8.8.8", "127.0.0.1", "255.255.255.255", "198.18.0.1"):
            r = await asgi(app, "POST", "/api/devices/add", json_body={"ip": ip, "model": "L930"})
            assert r.status == 400, ip
        r = await asgi(app, "POST", "/api/discover", json_body={"target": "8.8.8.255"})
        assert r.status == 400
    run(go())


def test_settings_never_expose_the_password(app_factory):
    async def go():
        core, app = await app_factory()
        await asgi(app, "POST", "/api/credentials", json_body={"email": "me@example.com", "password": "s3cret-pw"})
        r = await asgi(app, "GET", "/api/settings")
        assert "s3cret-pw" not in r.body.decode()
        assert "password_enc" not in r.json() and r.json()["has_password"] is True and r.json()["email"] == "me@example.com"
        assert "s3cret-pw" not in (await asgi(app, "GET", "/api/state")).body.decode()
    run(go())


def test_saved_items_are_sanitised(app_factory):
    async def go():
        _, app = await app_factory()
        post = lambda path, data: asgi(app, "POST", "/api/" + path, json_body=data)      # noqa: E731
        # scene data of any kind is rebuilt from known fields only
        assert (await post("scenes/save", {"name": "x", "kind": "power", "data": [1, 2]})).status == 400
        r = await post("scenes/save", {"name": "x\x00", "kind": "color", "data": {"h": "9999", "s": -4, "b": 1e9, "evil": "url(http://x)"}})
        assert r.status == 200 and r.json()["data"] == {"h": 360, "s": 1, "b": 100} and r.json()["name"] == "x"
        r = await post("scenes/save", {"name": "p", "kind": "paint", "data": {"colors": [["url(//x)", 0, 0]]}})
        assert r.status == 400
        # schedules need at least one day
        sch = {"collection": "schedules", "item": {"time": "07:00", "days": [], "action": {"type": "off"}}}
        assert (await post("collection/save", sch)).status == 400
        sch["item"]["days"] = [0, 1]
        assert (await post("collection/save", sch)).status == 200
        sch["item"]["time"] = "07:00\n"
        assert (await post("collection/save", sch)).status == 400                       # trailing newline slipped past ^$
        # palettes: only real hex colours survive
        r = await post("collection/save", {"collection": "palettes", "item": {"name": "p", "colors": ["#ff0000", "red", "#12345g", "#00ff00\n"]}})
        assert r.json()["colors"] == ["#ff0000"]
    run(go())


def test_names_with_markup_are_stored_as_plain_text(app_factory):
    async def go():
        _, app = await app_factory()
        evil = '"><img src=x onerror=alert(1)>'
        r = await asgi(app, "POST", "/api/scenes/save", json_body={"name": evil, "kind": "power", "data": {"on": True}})
        assert r.status == 200 and r.json()["name"] == evil[:40]          # stored as text; the UI only ever uses textContent
        js = open("tapo_studio/static/app.js", encoding="utf-8").read()
        assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
        assert not re.search(r"\beval\(|new Function\(", js)
    run(go())


def test_live_params_are_whitelisted_and_clamped(app_factory):
    async def go():
        core, app = await app_factory()
        r = await asgi(app, "POST", "/api/live/start", json_body={"mode": "animation", "params": {
            "animation": "flow", "speed": 1e9, "scale": -5, "evil": "x", "palette_colors": ["#ff0000", "javascript:1"]}})
        assert r.status == 200
        p = core.live.params
        assert p["speed"] == 5 and p["scale"] == 0.1 and "evil" not in p and p["palette_colors"] == ["#ff0000"]
        await asgi(app, "POST", "/api/live/stop", json_body={})
        assert (await asgi(app, "POST", "/api/live/start", json_body={"mode": "rm -rf"})).status == 400
    run(go())
