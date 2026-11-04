"""Local web server - locked down to this PC only.

Security model
--------------
* Listens on 127.0.0.1 only, so other devices on the network can't reach it.
* Host header must be 127.0.0.1/localhost, which blocks DNS-rebinding attacks
  where a website points its own domain at your PC.
* Every /api call needs a random token generated at each start. It is handed
  to your browser only in the URL fragment (#k=...), which browsers never send
  over the network and which isn't served by any URL, so other programs and
  other Windows accounts can't fetch it from the server. Cross-site requests
  (Origin / Sec-Fetch-Site) are rejected outright.
* POSTs must be JSON (forces a browser pre-flight that we never approve) and
  are size-limited.
* Strict Content-Security-Policy: no inline or external scripts, no framing.
* The app never runs shell commands, never reads or writes files chosen by a
  request, and only talks to private (home-network) IP addresses.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import effects as fx
from .core import Core, UserError, clean_name
from .driver import StripError

log = logging.getLogger("tapo-studio")
STATIC = Path(__file__).parent / "static"
MAX_BODY = 256 * 1024
HEX = re.compile(r"#[0-9a-fA-F]{6}")
TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _reject_constant(name):
    raise ValueError("NaN/Infinity are not allowed")


def build_app(core: Core, port: int, token: str) -> Starlette:
    token_b = token.encode()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    # ---- helpers --------------------------------------------------------
    async def body(req: Request) -> dict:
        if req.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise UserError("Expected JSON")
        buf, n = [], 0
        async for chunk in req.stream():
            n += len(chunk)
            if n > MAX_BODY:
                raise UserError("Request too large")
            buf.append(chunk)
        raw = b"".join(buf)
        try:
            data = json.loads(raw or b"{}", parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            raise UserError("Bad JSON")
        if not isinstance(data, dict):
            raise UserError("Bad request")
        return data

    def obj(v, what="data") -> dict:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise UserError(f"Bad {what}")
        return v

    def ids(v):
        if v is None:
            return None
        if not isinstance(v, list):
            raise UserError("targets must be a list")
        return [str(x)[:20] for x in v[:16]]

    def num_in(d, k, default=100):
        try:
            return max(1, min(100, int(float(d.get(k, default)))))
        except (TypeError, ValueError, OverflowError):
            return default

    def small(obj, limit=40_000):
        if len(json.dumps(obj)) > limit:
            raise UserError("That item is too large")
        return obj

    # ---- routes ---------------------------------------------------------
    async def index(req):
        # The page itself holds no secret; the key arrives in the URL fragment.
        return FileResponse(STATIC / "index.html", media_type="text/html")

    async def favicon(req):
        return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")

    async def state(req):
        return core.state()

    async def catalog(req):
        return core.catalog()

    async def settings(req):
        return core.store.public()

    async def credentials(req):
        d = await body(req)
        pw = d.get("password")
        await core.set_credentials(str(d.get("email", "")), None if pw in (None, "") else str(pw)[:200])
        return {"ok": True}

    async def discover(req):
        d = await body(req)
        return await core.discover(d.get("target") or None)

    async def add_device(req):
        d = await body(req)
        seg = d.get("segments")
        return await core.add_device(d.get("ip"), d.get("name"), int(seg) if seg else None, d.get("model"))

    async def update_device(req):
        d = await body(req)
        patch = {k: v for k, v in obj(d.get("patch"), "patch").items() if k in ("name", "segments", "reverse", "model")}
        await core.update_device(str(d.get("id")), patch)
        return {"ok": True}

    async def remove_device(req):
        d = await body(req)
        await core.remove_device(str(d.get("id")))
        return {"ok": True}

    async def diagnose(req):
        d = await body(req)
        return {"report": await core.diagnose(bool(d.get("test", True)))}

    async def paint_test(req):
        d = await body(req)
        return await core.paint_test(str(d.get("id")), int(d.get("index", 0)))

    async def paint_choose(req):
        d = await body(req)
        return await core.paint_choose(str(d.get("id")), str(d.get("key")))

    async def reconnect(req):
        await body(req)
        for s in core.strips.values():
            s.connected = False
        await core.connect_all()
        return {"ok": True}

    async def action(req):
        d = await body(req)
        return await core.action(ids(d.get("targets")), str(d.get("kind")), obj(d.get("params"), "params"))

    async def scene_save(req):
        d = await body(req)
        kind = str(d.get("kind"))
        data = small(d.get("data") or {})
        if not isinstance(data, dict):
            raise UserError("Bad scene data")
        num = lambda k, d, lo, hi: max(lo, min(hi, int(float(data.get(k, d)))))  # noqa: E731
        if kind == "effect":
            data = {"effect": fx.validate_lighting(data.get("effect") or {})}
        elif kind == "paint":
            cols = data.get("colors")
            if not isinstance(cols, list) or not cols:
                raise UserError("Nothing to save")
            data = {"colors": [fx.hsb(c) for c in cols][:100], "transition": num("transition", 300, 0, 5000),
                    "brightness": num("brightness", 100, 1, 100), "repeat": bool(data.get("repeat"))}
        elif kind == "color":
            data = {"h": num("h", 0, 0, 360), "s": num("s", 100, 1, 100), "b": num("b", 100, 1, 100)}
        elif kind == "white":
            data = {"k": num("k", 4000, 2500, 6500), "b": num("b", 100, 1, 100)}
        elif kind == "power":
            data = {"on": bool(data.get("on"))}
        elif kind == "lighting_preset":
            p = fx.preset_lighting(str(data.get("name", "")))
            if not p:
                raise UserError("Unknown effect")
            data = {"name": p["name"]}
        elif kind == "segment_preset":
            p = fx.preset_segment(str(data.get("enum", "")))
            if not p:
                raise UserError("Unknown scene")
            data = {"enum": p["enum"]}
        elif kind == "segment_custom":
            e = data.get("effect") or {}
            fx.validate_segment_effect(e, 50)
            data = {"effect": {"name": clean_name(e.get("name"), "Custom scene"), "type": str(e.get("type")),
                               "brightness": num_in(e, "brightness"), "uid": clean_name(e.get("uid"), "seg", 60),
                               "states": [fx.hsb(x) + [0] for x in e.get("states", [])][:100]}}
        else:
            raise UserError("Unknown scene type")
        sid = d.get("id")
        return core.save_scene(d.get("name"), kind, data, str(sid)[:20] if sid else None)

    async def scene_delete(req):
        d = await body(req)
        core.store.data["scenes"] = [x for x in core.store.data["scenes"] if x["id"] != d.get("id")]
        core.store.save()
        return {"ok": True}

    async def scene_apply(req):
        d = await body(req)
        sc = core.find_scene(d.get("id"))
        if not sc:
            raise UserError("Scene not found")
        return await core.apply_scene(sc, ids(d.get("targets")))

    def clean_item(coll: str, item: dict) -> dict:
        item = small(dict(item))
        iid = str(item.get("id") or secrets.token_hex(5))[:20]
        name = clean_name(item.get("name"), "Untitled")
        if coll == "effects":
            e = fx.validate_lighting({**item, "uid": iid})
            return {**e, "uid": iid, "id_key": iid, "name": name}
        if coll == "segment_effects":
            fx.validate_segment_effect(item, 50)
            return {"id": iid, "name": name, "type": item["type"], "brightness": int(item.get("brightness", 100)),
                    "states": [fx.hsb(s) + [0] for s in item.get("states", [])][:100]}
        if coll == "palettes":
            cols = [c for c in item.get("colors", []) if isinstance(c, str) and HEX.fullmatch(c)][:16]
            if len(cols) < 1:
                raise UserError("A palette needs at least one colour")
            return {"id": iid, "name": name, "colors": cols}
        if coll == "playlists":
            steps = [{"scene_id": str(s.get("scene_id"))[:20], "seconds": max(5, min(86400, int(s.get("seconds", 60))))}
                     for s in item.get("steps", [])][:100]
            return {"id": iid, "name": name, "shuffle": bool(item.get("shuffle")), "steps": steps}
        if coll == "schedules":
            t = str(item.get("time", ""))
            if not TIME.fullmatch(t):
                raise UserError("Time must be HH:MM")
            days = sorted({int(x) for x in item.get("days", range(7)) if 0 <= int(x) <= 6})
            if not days:
                raise UserError("Pick at least one day")
            a = obj(item.get("action"), "action")
            atype = str(a.get("type"))
            if atype not in ("scene", "on", "off", "playlist", "daylight", "preset"):
                raise UserError("Unknown schedule action")
            action = {"type": atype}
            for k in ("scene_id", "playlist_id", "name"):
                if k in a:
                    action[k] = str(a[k])[:60]
            if atype == "daylight":
                pr = obj(a.get("params"), "params")
                action["params"] = {k: str(pr[k]) for k in ("wake", "bed") if k in pr and TIME.fullmatch(str(pr[k]))}
            return {"id": iid, "name": name, "enabled": bool(item.get("enabled", True)), "time": t,
                    "days": days, "action": action, "targets": ids(item.get("targets")) or []}
        raise UserError("Unknown collection")

    COLL = {"effects", "segment_effects", "palettes", "playlists", "schedules"}

    async def coll_save(req):
        d = await body(req)
        coll = str(d.get("collection"))
        if coll not in COLL:
            raise UserError("Unknown collection")
        item = clean_item(coll, d.get("item") or {})
        key = "id_key" if coll == "effects" else "id"
        if not isinstance(core.store.data.get(coll), list):
            core.store.data[coll] = []
        lst = core.store.data[coll]
        lst[:] = [x for x in lst if x.get(key) != item[key]]
        if len(lst) >= 200:
            raise UserError("Limit reached")
        lst.append(item)
        core.store.save()
        return item

    async def coll_delete(req):
        d = await body(req)
        coll = str(d.get("collection"))
        if coll not in COLL:
            raise UserError("Unknown collection")
        key = "id_key" if coll == "effects" else "id"
        lst = core.store.data.get(coll) or []
        core.store.data[coll] = [x for x in lst if x.get(key) != d.get("id")]
        core.store.save()
        return {"ok": True}

    async def run_schedule(req):
        d = await body(req)
        sch = next((x for x in core.store.data["schedules"] if x["id"] == d.get("id")), None)
        if not sch:
            raise UserError("Schedule not found")
        await core.run_schedule_action(sch)
        return {"ok": True}

    LIVE_MODES = {"animation", "screen", "music", "daylight", "playlist"}

    LIVE_NUM = {"speed": (0.01, 5), "scale": (0.1, 10), "monitor": (1, 8), "depth": (0.02, 0.5),
                "saturation": (0, 3), "smoothing": (0, 0.95), "intensity": (0, 1), "sensitivity": (0, 1),
                "decay_ms": (50, 3000), "floor": (0, 1), "max_brightness": (1, 100)}
    LIVE_ENUM = {"trigger": {"bass", "snare", "hats", "vocals", "any"},
                 "style": {"flash", "color_step", "burst", "spectrum", "energy"},
                 "source": {"loopback", "microphone", "demo"},
                 "region": {"full", "top", "bottom", "left", "right", "around", "average"}}

    def live_params(p):
        p = small(obj(p, "params"), 8000)
        out = {}
        from .live import ANIMATIONS
        for k, v in p.items():
            if k in LIVE_NUM and isinstance(v, (int, float)) and not isinstance(v, bool):
                lo, hi = LIVE_NUM[k]
                out[k] = max(lo, min(hi, v))
            elif k in LIVE_ENUM and v in LIVE_ENUM[k]:
                out[k] = v
            elif k == "animation" and v in ANIMATIONS:
                out[k] = v
            elif k == "predict":
                out[k] = bool(v)
            elif k in ("wake", "bed") and isinstance(v, str) and TIME.fullmatch(v):
                out[k] = v
            elif k in ("palette", "playlist_id") and isinstance(v, str):
                out[k] = clean_name(v, "", 60)
            elif k == "palette_colors" and isinstance(v, list):
                out[k] = [c for c in v if isinstance(c, str) and HEX.fullmatch(c)][:16]
        return out

    async def live_start(req):
        d = await body(req)
        mode = str(d.get("mode"))
        if mode not in LIVE_MODES:
            raise UserError("Unknown mode")
        targets = [s.id for s in core.targets(ids(d.get("targets")))]
        try:
            await core.live.start(mode, live_params(d.get("params")), targets)
        except RuntimeError as e:
            raise UserError(str(e))
        return {"ok": True}

    async def live_update(req):
        d = await body(req)
        core.live.update(live_params(d.get("params")))
        return {"ok": True}

    async def live_stop(req):
        await body(req)
        await core.live.stop()
        return {"ok": True}

    async def prefs(req):
        d = await body(req)
        p = core.store.data["prefs"]
        for k, (lo, hi) in {"max_fps": (0.5, 10), "gamma": (1.0, 3.0), "sync_offset_ms": (-500, 1000)}.items():
            if k in d:
                p[k] = max(lo, min(hi, float(d[k])))
        for k in ("auto_transition", "join_strips"):
            if k in d:
                p[k] = bool(d[k])
        if d.get("theme") in ("auto", "dark", "light"):
            p["theme"] = d["theme"]
        core.store.save()
        return p

    async def preview_effect(req):
        d = await body(req)
        return fx.validate_lighting(d.get("effect") or {})

    def api(path, fn, methods=("POST",)):
        async def wrapped(req: Request):
            try:
                res = await fn(req)
                return JSONResponse(res, headers={"Cache-Control": "no-store"})
            except (UserError, fx.EffectError) as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            except StripError as e:          # the light itself refused: say what it said
                return JSONResponse({"error": f"The light refused the request: {str(e)[:200]}"}, status_code=400)
            except (ValueError, TypeError, KeyError, OverflowError):
                return JSONResponse({"error": "Invalid input"}, status_code=400)
            except Exception:
                log.exception("API error")
                return JSONResponse({"error": "Something went wrong - see the app window for details"}, status_code=500)
        return Route("/api/" + path, wrapped, methods=list(methods))

    G = ("GET",)
    routes = [
        Route("/", index), Route("/favicon.ico", favicon),
        api("state", state, G), api("catalog", catalog, G), api("settings", settings, G),
        api("credentials", credentials), api("discover", discover), api("devices/add", add_device),
        api("devices/update", update_device), api("devices/remove", remove_device),
        api("reconnect", reconnect), api("diagnose", diagnose), api("paint/test", paint_test), api("paint/choose", paint_choose), api("action", action),
        api("scenes/save", scene_save), api("scenes/delete", scene_delete), api("scenes/apply", scene_apply),
        api("collection/save", coll_save), api("collection/delete", coll_delete),
        api("schedules/run", run_schedule),
        api("live/start", live_start), api("live/update", live_update), api("live/stop", live_stop),
        api("prefs", prefs), api("effects/preview", preview_effect),
        Mount("/static", StaticFiles(directory=STATIC)),
    ]
    @contextlib.asynccontextmanager
    async def lifespan(_app):
        await core.start()
        yield

    app = Starlette(routes=routes, lifespan=lifespan)

    # ---- security middleware (pure ASGI so it wraps everything) ----------
    inner = app

    async def guard(scope, receive, send):
        if scope["type"] == "lifespan":
            return await inner(scope, receive, send)
        if scope["type"] != "http":
            return await Response(status_code=403)(scope, receive, send)
        req = Request(scope)
        host = req.headers.get("host", "")
        path = scope["path"]
        if host not in allowed_hosts:
            return await Response("Forbidden host", status_code=403)(scope, receive, send)
        origin = req.headers.get("origin")
        if origin and origin not in allowed_origins:
            return await Response("Forbidden origin", status_code=403)(scope, receive, send)
        if req.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
            return await Response("Forbidden", status_code=403)(scope, receive, send)
        if path.startswith("/api/"):
            sent = req.headers.get("x-studio-token", "").encode("utf-8", "replace")
            if not secrets.compare_digest(sent, token_b):
                return await JSONResponse({"error": "Not authorised"}, status_code=403)(scope, receive, send)
            if req.method not in ("GET", "POST"):
                return await Response(status_code=405)(scope, receive, send)
            cl = req.headers.get("content-length")
            if req.method == "POST" and not (cl and cl.isdigit()):
                return await Response(status_code=411)(scope, receive, send)
            if cl and cl.isdigit() and int(cl) > MAX_BODY:
                return await Response(status_code=413)(scope, receive, send)

        async def send_wrapper(msg):
            if msg["type"] == "http.response.start":
                h = list(msg.get("headers", []))
                h += [(b"content-security-policy", CSP.encode()), (b"x-frame-options", b"DENY"),
                      (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                      (b"cross-origin-opener-policy", b"same-origin"),
                      (b"cross-origin-resource-policy", b"same-origin"),
                      (b"permissions-policy", b"camera=(), microphone=(), geolocation=()")]
                if not path.startswith("/static/"):
                    h.append((b"cache-control", b"no-store"))
                msg["headers"] = h
            await send(msg)

        return await inner(scope, receive, send_wrapper)

    return guard
