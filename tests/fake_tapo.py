"""A stand-in for the `tapo` library, modelled on its published type stubs.

It lets us exercise `TapoBackend` (the real-hardware code path) without any
hardware or network. It is deliberately strict: calling a method that the real
library doesn't have raises AttributeError, which catches typos in the driver.
The real library is checked separately in test_library_contract.py.
"""

from __future__ import annotations

import sys
import types

LIGHTING_BUILDERS = {
    "with_brightness": "brightness", "with_is_custom": "custom", "with_display_colors": "display_colors",
    "with_enabled": "enable", "with_id": "id", "with_name": "name", "with_type": "type",
    "with_backgrounds": "backgrounds", "with_brightness_range": "brightness_range",
    "with_direction": "direction", "with_duration": "duration", "with_expansion_strategy": "expansion_strategy",
    "with_fade_off": "fadeoff", "with_hue_range": "hue_range", "with_init_states": "init_states",
    "with_random_seed": "random_seed", "with_repeat_times": "repeat_times", "with_run_time": "run_time",
    "with_saturation_range": "saturation_range", "with_segment_length": "segment_length",
    "with_segments": "segments", "with_sequence": "sequence", "with_spread": "spread",
    "with_transition": "transition", "with_transition_range": "transition_range",
    "with_transition_sequence": "trans_sequence",
}
SEGMENT_BUILDERS = {
    "with_brightness": "brightness", "with_is_custom": "custom", "with_display_colors": "display_colors",
    "with_enabled": "enable", "with_id": "id", "with_name": "name", "with_type": "type",
    "with_device_type": "deviceType", "with_segments": "segments", "with_states": "states",
}


class _Enum:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


def _enum_class(cls_name, members):
    return type(cls_name, (), {m: _Enum(m) for m in members})


LightingEffectType = _enum_class("LightingEffectType", ["Sequence", "Random", "Pulse", "Static"])
SegmentEffectType = _enum_class("SegmentEffectType",
                                ["Circulating", "Breathe", "Chasing", "Flicker", "Bloom", "Stacking", "NONE"])
LightingEffectPreset = _enum_class("LightingEffectPreset", [
    "Aurora", "BubblingCauldron", "CandyCane", "Christmas", "Flicker", "GrandmasChristmasLights", "Hanukkah",
    "HauntedMansion", "Icicle", "Lightning", "Ocean", "Rainbow", "Raindrop", "Spring", "Sunrise", "Sunset", "Valentines"])


def _seg_presets():
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).resolve().parent.parent / "tapo_studio/data/segment_presets.json").read_text())
    return [p["enum"] for p in data]


SegmentEffectPreset = _enum_class("SegmentEffectPreset", _seg_presets())


class _Builder:
    _map: dict = {}

    def __init__(self, name, type_, is_custom, enabled, brightness, display_colors):
        self.d = {"name": name, "type": type_.name.lower(), "custom": int(is_custom), "enable": int(enabled),
                  "brightness": brightness, "display_colors": [list(c) for c in display_colors]}

    def __getattr__(self, item):
        if item in self._map:
            key = self._map[item]

            def setter(value):
                self.d[key] = [list(v) if isinstance(v, tuple) else v for v in value] if isinstance(value, (list, tuple)) else value
                return self
            return setter
        raise AttributeError(item)

    def to_dict(self):
        return self.d


class LightingEffect(_Builder):
    _map = LIGHTING_BUILDERS


class SegmentEffect(_Builder):
    _map = SEGMENT_BUILDERS


def _clear(handler):
    handler.effect = {"enable": 0}          # a plain colour command removes any running effect or scene
    handler.scene = {"enable": 0}
    handler.cleared = True


class _SetBuilder:
    def __init__(self):
        self.p = {}

    def on(self):
        self.p["on"] = True
        return self

    def off(self):
        self.p["on"] = False
        return self

    def brightness(self, v):
        self.p["brightness"] = v
        return self

    def hue_saturation(self, h, s):
        self.p["hue"], self.p["saturation"] = h, s
        return self

    def color_temperature(self, k):
        self.p["color_temp"] = k
        return self

    async def send(self, handler):
        handler.calls.append(("set", dict(self.p)))
        if "hue" in self.p or "color_temp" in self.p:
            _clear(handler)


class _Handler:
    model = "?"

    def __init__(self, ip):
        self.ip = ip
        self.calls = []
        self.effect = {"enable": 0}
        self.scene = {"enable": 0}
        self.cleared = False

    async def on(self):
        self.calls.append(("on",))

    async def off(self):
        self.calls.append(("off",))

    async def set_brightness(self, v):
        self.calls.append(("set_brightness", v))

    async def get_device_info_json(self):
        return {"model": self.model, "device_on": True, "brightness": 80, "mac": INFO_MAC.get(self.ip, "AA-BB-CC-DD-EE-01"),
                "lighting_effect": self.effect, "segment_effect": self.scene if not SEGMENT_ON[0] else {"enable": 1, "id": "TapoStrip_other", "name": "Disco"},
                "music_rhythm_enable": MUSIC_SYNC_ON[0], "color_temp": 0, "fw_ver": "1.0"}

    async def device_reboot(self, delay_s):
        self.calls.append(("reboot", delay_s))


class LightHandler(_Handler):
    """L510 / L520 / L610: brightness only (no set(), no colour)."""


class ColorLightHandler(_Handler):
    def set(self):
        return _SetBuilder()

    async def set_color(self, c):
        self.calls.append(("set_color", c))

    async def set_hue_saturation(self, h, s):
        self.calls.append(("set_hue_saturation", h, s))

    async def set_color_temperature(self, k):
        self.calls.append(("set_color_temperature", k))


class RgbLightStripHandler(ColorLightHandler):
    pass


class RgbicLightStripHandler(ColorLightHandler):
    async def set_lighting_effect(self, effect):
        if not isinstance(effect, (LightingEffect, _Enum)):
            raise TypeError("expected LightingEffect or LightingEffectPreset")
        self.calls.append(("set_lighting_effect", effect.to_dict() if isinstance(effect, LightingEffect) else effect.name))
        d = effect.to_dict() if isinstance(effect, LightingEffect) else {"id": "preset", "name": effect.name}
        is_preset = not isinstance(effect, LightingEffect)
        shown = (not IGNORE_ALL[0] and d["id"] not in IGNORE_IDS and (not NEEDS_CLEAR[0] or self.cleared)
                 and (is_preset or not CUSTOM_LIGHTING_IGNORED[0]))
        self.cleared = False
        if shown:
            self.effect = {"enable": 1, "id": d["id"], "name": d["name"], "display_colors": d.get("display_colors")}
            self.scene = {"enable": 0}
        # a light that ignores the command keeps showing whatever it showed before

    async def set_segment_effect(self, effect):
        if not isinstance(effect, (SegmentEffect, _Enum)):
            raise TypeError("expected SegmentEffect or SegmentEffectPreset")
        self.calls.append(("set_segment_effect", effect.to_dict() if isinstance(effect, SegmentEffect) else effect.name))
        d = effect.to_dict() if isinstance(effect, SegmentEffect) else {"id": "preset", "name": effect.name}
        if isinstance(effect, SegmentEffect):
            # What a real L930 on firmware 1.4.3 enforces (error -1008 "PARAMS"):
            segs, states = d.get("segments") or [], d.get("states") or []
            if (len(d.get("display_colors") or []) > 4 or len(states) != len(segs)
                    or any(b <= a for a, b in zip(segs, segs[1:])) or (segs and segs[-1] > 49)):
                raise RuntimeError("error_code -1008 PARAMS")
        if not IGNORE_ALL[0] and (not isinstance(effect, SegmentEffect) or SEGMENT_RULE[0](d)):
            self.scene = {"enable": 1, "id": d["id"], "name": d["name"]}
            self.effect = {"enable": 0}


_FACTORIES = {"l510": LightHandler, "l520": LightHandler, "l610": LightHandler,
              "l430": ColorLightHandler, "l530": ColorLightHandler, "l535": ColorLightHandler,
              "l630": ColorLightHandler, "l900": RgbLightStripHandler,
              "l920": RgbicLightStripHandler, "l930": RgbicLightStripHandler}

#: What the fake network "contains", for discover_devices_raw.
NETWORK: dict[str, dict] = {}
HANDLERS: dict[str, _Handler] = {}
LOGINS: list[str] = []      # IPs we sent credentials to
IGNORE_IDS: set[str] = set()    # effect ids the fake light silently ignores (answers "ok", shows nothing)
IGNORE_ALL = [False]
CUSTOM_LIGHTING_IGNORED = [False]   # newer firmware: custom lighting effects are accepted but never applied
SEGMENT_RULE = [lambda d: True]      # which scene-style effects the light applies (gets the effect dict)
NEEDS_CLEAR = [False]           # accepts an effect only straight after a plain colour command
SEGMENT_ON = [False]            # reports a scene animation as running
MUSIC_SYNC_ON = [False]
INFO_MAC: dict[str, str] = {}   # what each light reports about itself after login (may differ in format from discovery)


class _RawResult:
    def __init__(self, ip, message):
        self.ip, self.message = ip, message


class _MaybeRaw:
    def __init__(self, r):
        self.r = r

    def get(self):
        return self.r


class _AsyncIter:
    def __init__(self, items):
        self.items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


class ApiClient:
    def __init__(self, tapo_username, tapo_password, timeout_s=None):
        self.user, self.pw = tapo_username, tapo_password

    @staticmethod
    async def discover_devices_raw(target, timeout_s=10):
        items = []
        for ip, res in NETWORK.items():
            if target.endswith(".255") or target == ip:
                items.append(_MaybeRaw(_RawResult(ip, {"result": res})))
        return _AsyncIter(items)


def _factory(name, cls):
    async def make(self, ip_address):
        LOGINS.append(ip_address)          # a handler is only created by logging in
        h = cls(ip_address)
        h.model = NETWORK.get(ip_address, {}).get("device_model", name.upper())
        HANDLERS[ip_address] = h
        return h
    return make


for _n, _c in _FACTORIES.items():
    setattr(ApiClient, _n, _factory(_n, _c))


def install(monkeypatch):
    """Make `import tapo` return this fake for the duration of a test."""
    tapo = types.ModuleType("tapo")
    tapo.ApiClient = ApiClient
    req = types.ModuleType("tapo.requests")
    for n in ("LightingEffect", "LightingEffectPreset", "LightingEffectType",
              "SegmentEffect", "SegmentEffectPreset", "SegmentEffectType"):
        setattr(req, n, globals()[n])
    tapo.requests = req
    monkeypatch.setitem(sys.modules, "tapo", tapo)
    monkeypatch.setitem(sys.modules, "tapo.requests", req)
    NETWORK.clear()
    HANDLERS.clear()
    LOGINS.clear()
    INFO_MAC.clear()
    IGNORE_IDS.clear()
    IGNORE_ALL[0] = False
    NEEDS_CLEAR[0] = False
    CUSTOM_LIGHTING_IGNORED[0] = False
    SEGMENT_RULE[0] = lambda d: True
    SEGMENT_ON[0] = False
    MUSIC_SYNC_ON[0] = False


def announce(ip, model, mac="AA:BB:CC:DD:EE:01", encrypt="KLAP"):
    NETWORK[ip] = {"device_model": model, "mac": mac, "device_id": "dev" + ip.replace(".", ""),
                   "mgt_encrypt_schm": {"encrypt_type": encrypt, "is_support_https": False}}
