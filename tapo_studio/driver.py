"""Talking to the lights.

`TapoBackend` uses the open-source `tapo` library (local network only, no cloud).
`MockBackend` fakes a few lights so the whole app can be tried without hardware
(run with --mock).

A backend is a small object with these async methods, so adding support for
another kind of light means writing one more class like these:

    set_credentials(email, password)      discover(target, timeout)
    connect(strip)    refresh(strip)      forget(strip)
    power(strip, on)  brightness(strip, bri)
    color(strip, hue, sat, bri)           white(strip, kelvin, bri)
    lighting(strip, effect_dict)          lighting_preset(strip, name)
    segment_preset(strip, enum_name)      segment_custom(strip, effect_dict)
    reboot(strip)

Each `Strip` serialises its own requests with a lock: the device handles one
command at a time, and stacking requests only makes it fall behind.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import socket
import time
from collections import deque

from . import devices
from . import effects as fx

log = logging.getLogger("tapo-studio")


def preset_enum_name(name: str) -> str:
    """'Grandma's Christmas Lights' -> 'GrandmasChristmasLights'."""
    return "".join(w.capitalize() for w in "".join(ch if ch.isalnum() or ch == " " else "" for ch in name).split())


def local_broadcast() -> str:
    """Best guess at the LAN broadcast address (assumes a /24 home network)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return str(ipaddress.ip_network(f"{ip}/24", strict=False).broadcast_address)
    except OSError:
        return "192.168.1.255"


class StripError(RuntimeError):
    pass


def normalise_mac(m) -> str | None:
    h = "".join(ch for ch in str(m or "").upper() if ch in "0123456789ABCDEF")
    return h if len(h) == 12 else None


class Strip:
    """One physical strip and everything we remember about it."""

    def __init__(self, dev_id: str, ip: str, name: str, segments: int = 50, model: str = "L930"):
        self.id = dev_id
        self.ip = ip
        self.name = name
        self.model = model
        self.segments = segments
        self.reverse = False
        self.mac: str | None = None        # hardware ID as announced during discovery
        self.info_mac: str | None = None   # hardware ID as reported by the light after login
        self.connected = False
        # How patterns must be sent to this light, learned by reading back what it shows (Core._confirm_effect):
        # "fixed" = re-use one effect id, "fresh" = a new id each time, "clear" = also clear the running effect first.
        self.apply_mode = "fixed"
        self.paint_method = fx.DEFAULT_PAINT_METHOD     # which request paints zones on this light (see effects.py)
        self.error: str | None = None
        self.lock = asyncio.Lock()
        self.rtt = deque(maxlen=40)
        self.info: dict = {}
        # What we last asked the strip to show, so brightness changes can
        # re-send the running effect instead of cancelling it.
        self.mode = "unknown"      # color | white | effect | preset | segment | frame
        self.last_effect: dict | None = None
        self.frame: list[list[int]] = [[0, 0, 0]] * segments
        self.brightness = 100
        self.on = True

    @property
    def caps(self) -> frozenset:
        return devices.caps_for(self.model)

    def public(self) -> dict:
        r = sorted(self.rtt)
        return {
            "id": self.id, "ip": self.ip, "name": self.name, "segments": self.segments,
            "connected": self.connected, "error": self.error, "mode": self.mode,
            "on": self.on, "brightness": self.brightness, "frame": self.frame,
            "write_ms": round(r[len(r) * 3 // 4] * 1000) if r else None,
            "model": self.model, "caps": sorted(self.caps),
            "family": (devices.family_for(self.model) or devices.FAMILIES["white_bulb"]).label,
            "fw": self.info.get("fw_ver"), "paint_method": self.paint_method,
            "rssi": self.info.get("rssi"),
            "active_effect": (self.info.get("lighting_effect") or {}).get("name")
            if (self.info.get("lighting_effect") or {}).get("enable") else None,
        }

    async def timed(self, coro):
        t = time.perf_counter()
        async with self.lock:
            try:
                res = await coro
            except Exception as e:  # library raises its own error types
                self.error = str(e)
                raise StripError(str(e)) from e
        self.rtt.append(time.perf_counter() - t)
        self.error = None
        return res


# ---------------------------------------------------------------------------
# Real hardware
# ---------------------------------------------------------------------------

class TapoBackend:
    name = "tapo"

    def __init__(self):
        import tapo  # noqa: F401  (fail early with a clear message if missing)
        from tapo import ApiClient
        from tapo.requests import (LightingEffect, LightingEffectPreset, LightingEffectType,
                                   SegmentEffect, SegmentEffectPreset, SegmentEffectType)
        self._ApiClient = ApiClient
        self.LE, self.LEP, self.LET = LightingEffect, LightingEffectPreset, LightingEffectType
        self.SE, self.SEP, self.SET = SegmentEffect, SegmentEffectPreset, SegmentEffectType
        self.client = None
        self.handlers: dict[str, object] = {}

    def set_credentials(self, email: str, password: str):
        self.client = self._ApiClient(email, password, timeout_s=8) if email and password else None
        self.handlers.clear()

    def _need_client(self):
        if self.client is None:
            raise StripError("Add your Tapo email and password in Settings first.")
        return self.client

    async def _probe(self, target: str, timeout: int) -> list[dict]:
        """No-login discovery: the devices' own announcements. Your password is
        never sent to anything during a search."""
        out = []
        raw = await self._ApiClient.discover_devices_raw(target, timeout_s=timeout)
        async for item in raw:
            try:
                r = item.get()
            except Exception:
                continue
            res = (r.message or {}).get("result") or {}
            model = str(res.get("device_model") or "")
            out.append({
                "ip": r.ip, "model": model.split("(")[0] or "Unknown",
                "mac": normalise_mac(res.get("mac")),
                "name": "", "device_id": str(res.get("device_id") or "")[:12],
                "encrypt": str((res.get("mgt_encrypt_schm") or {}).get("encrypt_type") or ""),
                "supported": devices.base_model(model) is not None,
            })
        return out

    async def discover(self, target: str, timeout: int = 4) -> list[dict]:
        found = await self._probe(target, timeout)
        for f in found:
            fam = devices.family_for(f["model"])
            f["name"] = f"{f['model']} {fam.label.lower()}" if fam else f["model"]
        return found

    async def connect(self, strip: Strip):
        client = self._need_client()
        if strip.ip not in self.handlers:
            # Probe first (no login): opens the port on some firmware, and lets
            # us check this is the same strip before any credentials are used.
            try:
                seen = await self._probe(strip.ip, 2)
            except Exception:
                seen = []
            me = next((x for x in seen if x["ip"] == strip.ip), None)
            if me and strip.mac and me["mac"] and me["mac"] != strip.mac:
                raise StripError("A different device is now using this IP address, so Tapo Studio did not "
                                 "send it your login. Remove and re-add the light if you replaced it.")
            if me and me["mac"] and not strip.mac:
                strip.mac = me["mac"]                                  # remember it for next time
            if me and not me["supported"]:
                raise StripError(f"The device at {strip.ip} is a {me['model']}, which Tapo Studio doesn't support.")
            if me and me["encrypt"] == "AES":
                raise StripError("This device asked for an old, insecure login method, so it was refused.")
            if me:
                strip.model = devices.base_model(me["model"]) or strip.model   # trust the device over our notes
            factory = getattr(client, devices.factory_name(strip.model) or "", None)
            if factory is None:
                raise StripError(f"Tapo Studio doesn't support the model {strip.model!r}.")
            self.handlers[strip.ip] = await factory(strip.ip)
        strip.connected = True
        info = await self.refresh(strip)
        # Each source is only ever compared with itself: the discovery announcement and the
        # light's own report of its hardware ID need not be formatted (or even derived) alike.
        mac = normalise_mac(info.get("mac"))
        if strip.info_mac and mac and mac != strip.info_mac:
            self.handlers.pop(strip.ip, None)
            strip.connected = False
            raise StripError("This isn't the light that was saved at this address (its hardware ID changed). "
                             "If you replaced it, remove it and add it again.")
        strip.info_mac = strip.info_mac or mac

    def forget(self, strip: Strip):
        self.handlers.pop(strip.ip, None)

    def h(self, strip: Strip):
        if strip.ip not in self.handlers:
            raise StripError(f"{strip.name} is not connected")
        return self.handlers[strip.ip]

    async def refresh(self, strip: Strip):
        info = await strip.timed(self.h(strip).get_device_info_json())
        strip.info = info
        strip.on = bool(info.get("device_on"))
        if info.get("brightness"):
            strip.brightness = info["brightness"]
        if strip.mode == "unknown":
            le = info.get("lighting_effect") or {}
            se = info.get("segment_effect") or {}
            strip.mode = "effect" if le.get("enable") else "segment" if se.get("enable") else (
                "white" if info.get("color_temp") else "color")
            if "color" not in strip.caps:
                strip.mode = "white"
        return info

    async def power(self, strip: Strip, on: bool):
        h = self.h(strip)
        await strip.timed(h.on() if on else h.off())
        strip.on = on

    async def color(self, strip: Strip, hue: int, sat: int, bri: int):
        h = self.h(strip)
        await strip.timed(h.set().on().hue_saturation(int(hue), max(1, int(sat))).brightness(max(1, int(bri))).send(h))

    async def white(self, strip: Strip, kelvin: int, bri: int):
        h = self.h(strip)
        await strip.timed(h.set().on().color_temperature(int(kelvin)).brightness(max(1, int(bri))).send(h))

    async def brightness(self, strip: Strip, bri: int):
        await strip.timed(self.h(strip).set_brightness(max(1, int(bri))))

    def _lighting(self, d: dict):
        e = self.LE(d["name"], getattr(self.LET, d["type"].capitalize()), bool(d.get("custom", 1)), True,
                    int(d["brightness"]), [tuple(c) for c in d["display_colors"]])
        e.with_id(d["id"])
        tup = lambda v: tuple(v)  # noqa: E731
        cols = lambda v: [tuple(c) for c in v]  # noqa: E731
        conv = {
            "backgrounds": ("with_backgrounds", cols), "init_states": ("with_init_states", cols),
            "sequence": ("with_sequence", cols),
            "brightness_range": ("with_brightness_range", tup), "hue_range": ("with_hue_range", tup),
            "saturation_range": ("with_saturation_range", tup),
            "transition_range": ("with_transition_range", tup),
            "segments": ("with_segments", list), "trans_sequence": ("with_transition_sequence", list),
            "direction": ("with_direction", int), "duration": ("with_duration", int),
            "expansion_strategy": ("with_expansion_strategy", int), "fadeoff": ("with_fade_off", int),
            "random_seed": ("with_random_seed", int), "repeat_times": ("with_repeat_times", int),
            "run_time": ("with_run_time", int), "segment_length": ("with_segment_length", int),
            "spread": ("with_spread", int), "transition": ("with_transition", int),
        }
        for key, (method, f) in conv.items():
            if key in d and d[key] is not None:
                getattr(e, method)(f(d[key]))
        return e

    async def lighting(self, strip: Strip, d: dict):
        await strip.timed(self.h(strip).set_lighting_effect(self._lighting(d)))

    async def lighting_preset(self, strip: Strip, name: str):
        enum = getattr(self.LEP, preset_enum_name(name), None)
        if enum is None:
            raise StripError(f"Unknown preset {name}")
        await strip.timed(self.h(strip).set_lighting_effect(enum))

    async def segment_preset(self, strip: Strip, enum_name: str):
        enum = getattr(self.SEP, enum_name, None)
        if enum is None:
            raise StripError(f"Unknown scene {enum_name}")
        await strip.timed(self.h(strip).set_segment_effect(enum))

    async def segment_custom(self, strip: Strip, d: dict):
        t = "NONE" if d["type"] == "none" else d["type"].capitalize()
        states = [tuple(s) for s in d["states"]]
        # The strip rejects (error -1008) a scene effect with more than 4 display colours.
        e = self.SE(d["name"], getattr(self.SET, t), True, True, int(d["brightness"]), states[:4])
        e.with_id(d["id"])
        e.with_segments(list(d["segments"]))
        e.with_states(states)
        await strip.timed(self.h(strip).set_segment_effect(e))

    async def reboot(self, strip: Strip):
        await strip.timed(self.h(strip).device_reboot(3))


# ---------------------------------------------------------------------------
# Fake hardware for trying the app out
# ---------------------------------------------------------------------------

class MockBackend:
    name = "mock"

    def __init__(self):
        self.client = True
        self.sent: list[tuple[str, str, object]] = []
        self.ignored_ids: set[str] = set()     # effect ids this fake light silently ignores
        self.ignore_all = False
        self.needs_clear = False               # this fake light only accepts an effect right after a plain colour
        self.custom_lighting_ignored = False   # newer-firmware behaviour: custom lighting effects are never applied
        self.segment_accept = lambda d: True   # which scene-style effects this fake light applies

    def set_credentials(self, email, password):
        pass

    async def _io(self, strip: Strip, what: str, payload=None):
        async def job():
            await asyncio.sleep(random.uniform(0.18, 0.32))  # real strips: ~0.3 s per write
            self.sent.append((strip.id, what, payload))
            del self.sent[:-200]
        return await strip.timed(job())

    async def discover(self, target, timeout=4):
        await asyncio.sleep(0.5)
        return [
            {"ip": "192.168.1.50", "model": "L930", "name": "Desk strip (demo)", "mac": "A1B2C3D4E5F0", "supported": True},
            {"ip": "192.168.1.51", "model": "L930", "name": "TV strip (demo)", "mac": "A1B2C3D4E5F1", "supported": True},
            {"ip": "192.168.1.52", "model": "L530", "name": "Lamp (demo)", "mac": "A1B2C3D4E5F2", "supported": True},
            {"ip": "192.168.1.60", "model": "P110", "name": "Kettle plug (demo)", "mac": "A1B2C3D4E5F3", "supported": False},
        ]

    def forget(self, strip):
        pass

    async def connect(self, strip):
        await asyncio.sleep(0.2)
        strip.connected = True
        strip.info = {"model": strip.model, "fw_ver": "1.2.5 (demo)", "rssi": -52, "device_on": True}
        if strip.mode == "unknown":
            strip.mode = "color"

    async def refresh(self, strip):
        await self._io(strip, "get_device_info")
        return strip.info

    async def power(self, strip, on):
        await self._io(strip, "on" if on else "off")
        strip.on = on

    async def color(self, strip, hue, sat, bri):
        await self._io(strip, "color", (hue, sat, bri))
        strip.info["lighting_effect"] = {"enable": 0}          # a plain colour removes any running effect
        strip._cleared = True

    async def white(self, strip, kelvin, bri):
        await self._io(strip, "white", (kelvin, bri))

    async def brightness(self, strip, bri):
        await self._io(strip, "brightness", bri)

    async def lighting(self, strip, d):
        await self._io(strip, "set_lighting_effect", d)
        ok = (not self.ignore_all and not self.custom_lighting_ignored and d.get("id") not in self.ignored_ids
              and (not self.needs_clear or getattr(strip, "_cleared", False)))       # tests make a light misbehave
        strip._cleared = False
        if ok:
            strip.info["lighting_effect"] = {"enable": 1, "id": d.get("id"), "name": d.get("name"),
                                             "display_colors": d.get("display_colors")}
        # a light that ignores the command carries on showing whatever it showed before

    async def lighting_preset(self, strip, name):
        await self._io(strip, "lighting_preset", name)
        strip.info["lighting_effect"] = {"enable": 1, "id": "TapoStrip_" + name, "name": name}
        strip.info["segment_effect"] = {"enable": 0}

    async def segment_preset(self, strip, enum_name):
        await self._io(strip, "segment_preset", enum_name)
        strip.info["segment_effect"] = {"enable": 1, "id": "TapoStrip_" + enum_name, "name": enum_name}

    async def segment_custom(self, strip, d):
        segs, states = d.get("segments") or [], d.get("states") or []
        if len(states) != len(segs) or any(b <= a for a, b in zip(segs, segs[1:])):
            async def reject():                      # a real light answers a mismatched band list with error -1008
                raise RuntimeError("error_code -1008 PARAMS")
            await strip.timed(reject())
        await self._io(strip, "segment_custom", d)
        if not self.ignore_all and self.segment_accept(d):
            strip.info["segment_effect"] = {"enable": 1, "id": d.get("id"), "name": d.get("name")}
            strip.info["lighting_effect"] = {"enable": 0}

    async def reboot(self, strip):
        await self._io(strip, "reboot")


def make_backend(mock: bool):
    if mock:
        return MockBackend()
    try:
        return TapoBackend()
    except ImportError:
        log.warning("The 'tapo' package is not installed - starting in demo mode.")
        return MockBackend()
