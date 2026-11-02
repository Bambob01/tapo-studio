"""Effect models, validation and presets.

Everything in the app is kept as plain dicts in the *wire format* the strip
understands (the same JSON the device reports back). Only the real driver turns
these dicts into `tapo` library objects, right before sending. That keeps the
UI, saved scenes and the mock driver free of any library dependency.
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import uuid
from pathlib import Path

DATA = Path(__file__).parent / "data"

LIGHTING_PRESETS: list[dict] = json.loads((DATA / "lighting_presets.json").read_text())
SEGMENT_PRESETS: list[dict] = json.loads((DATA / "segment_presets.json").read_text())

LIGHTING_TYPES = ["static", "sequence", "random", "pulse"]
SEGMENT_TYPES = ["circulating", "breathe", "chasing", "flicker", "bloom", "stacking"]

# field -> (kind, min, max). kind: int | color | colors | range | ints
_FIELDS = {
    "brightness": ("int", 0, 100),
    "direction": ("int", 0, 255),
    "duration": ("int", 0, 3_600_000),
    "expansion_strategy": ("int", 0, 255),
    "fadeoff": ("int", 0, 65535),
    "random_seed": ("int", 0, 2**31),
    "repeat_times": ("int", 0, 255),
    "run_time": ("int", 0, 3_600_000),
    "segment_length": ("int", 0, 255),
    "spread": ("int", 0, 255),
    "transition": ("int", 0, 65535),
    "hue_range": ("range", 0, 360),
    "saturation_range": ("range", 0, 100),
    "brightness_range": ("range", 0, 100),
    "transition_range": ("range", 0, 65535),
    "segments": ("ints", 0, 255),
    "trans_sequence": ("ints", 0, 65535),
    "sequence": ("colors", 0, 0),
    "display_colors": ("colors", 0, 0),
    "backgrounds": ("colors", 0, 0),
    "init_states": ("colors", 0, 0),
}
MAX_COLORS = 100


class EffectError(ValueError):
    pass


def _clamp(v, lo, hi) -> int:
    if isinstance(v, bool):
        v = int(v)
    try:
        v = int(round(float(v)))
    except (TypeError, ValueError, OverflowError):
        raise EffectError("expected a number")
    return max(lo, min(hi, v))


def clean_name(s, default="Untitled", n=40) -> str:
    """Printable text only (no control or bidi-override characters)."""
    s = "".join(ch for ch in str(s if s is not None else "")
                if ch.isprintable() and ch not in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069").strip()
    return (s or default)[:n]


def hsb(c) -> list[int]:
    """Clamp one (hue 0-360, saturation 0-100, brightness 0-100) colour."""
    if not isinstance(c, (list, tuple)) or len(c) < 3:
        raise EffectError("each colour must be [hue, saturation, brightness]")
    return [_clamp(c[0], 0, 360), _clamp(c[1], 0, 100), _clamp(c[2], 0, 100)]


def effect_id(seed: str) -> str:
    """32 hex chars. The strip silently ignores effects whose id is any other shape."""
    return hashlib.md5(seed.encode()).hexdigest()


def new_id() -> str:
    return uuid.uuid4().hex


def validate_lighting(effect: dict) -> dict:
    """Return a clean wire-format lighting effect, clamped to safe ranges."""
    if not isinstance(effect, dict):
        raise EffectError("effect must be an object")
    effect = {k: v for k, v in effect.items() if k in _FIELDS or k in ("name", "type", "custom", "uid", "id")}
    t = str(effect.get("type", "static")).lower()
    if t not in LIGHTING_TYPES:
        raise EffectError(f"type must be one of {LIGHTING_TYPES}")
    out: dict = {
        "name": clean_name(effect.get("name"), "Custom effect"),
        "type": t,
        "custom": 1 if effect.get("custom", 1) else 0,
        "enable": 1,
        "brightness": _clamp(effect.get("brightness", 100), 1, 100),
    }
    for key, (kind, lo, hi) in _FIELDS.items():
        if key == "brightness" or key not in effect or effect[key] is None:
            continue
        v = effect[key]
        if kind == "int":
            out[key] = _clamp(v, lo, hi)
        elif kind == "range":
            if not isinstance(v, (list, tuple)) or len(v) < 2:
                raise EffectError(f"{key} must be [from, to]")
            a, b = (_clamp(v[0], lo, hi), _clamp(v[1], lo, hi))
            out[key] = [min(a, b), max(a, b)]
        elif kind == "ints":
            if not isinstance(v, (list, tuple)):
                raise EffectError(f"{key} must be a list")
            out[key] = [_clamp(x, lo, hi) for x in list(v)[:MAX_COLORS]]
        elif kind == "colors":
            if not isinstance(v, (list, tuple)):
                raise EffectError(f"{key} must be a list of colours")
            cols = [hsb(c) for c in list(v)[:MAX_COLORS]]
            if cols:
                out[key] = cols
    if t in ("static", "sequence", "pulse") and not out.get("sequence"):
        raise EffectError("this effect type needs at least one colour in its sequence")
    # Keep payloads within what the strip's firmware comfortably accepts.
    out["segments"] = [x for x in out.get("segments", [0]) if x < MAX_COLORS][:MAX_COLORS] or [0]
    out.setdefault("expansion_strategy", 1)
    total = sum(len(out.get(k, [])) for k in ("sequence", "backgrounds", "init_states", "trans_sequence"))
    if total > 120:
        raise EffectError("That effect has too many colours for the strip (max 120 in total)")
    if out.get("display_colors"):
        out["display_colors"] = out["display_colors"][:8]
    if not out.get("display_colors"):
        src = out.get("sequence") or out.get("init_states") or out.get("backgrounds") or [[0, 0, 100]]
        out["display_colors"] = src[:8]
    # The device will not change an existing effect's type in place, so the id
    # is derived from the effect's own key *and* its type.
    key = str(effect.get("uid") or effect.get("id") or out["name"])
    out["id"] = effect_id(f"{key}:{t}")
    return out


def validate_segment_effect(effect: dict, segment_count: int) -> dict:
    if not isinstance(effect, dict):
        raise EffectError("effect must be an object")
    t = str(effect.get("type", "circulating")).lower()
    if t not in SEGMENT_TYPES:
        raise EffectError(f"type must be one of {SEGMENT_TYPES}")
    states = []
    raw = effect.get("states") or []
    if not isinstance(raw, (list, tuple)):
        raise EffectError("states must be a list of colours")
    for s in list(raw)[:MAX_COLORS]:
        c = hsb(s)
        states.append(c + [_clamp(s[3] if len(s) > 3 else 0, 0, 65535)])
    if not states:
        raise EffectError("add at least one colour")
    n = max(1, int(segment_count))
    states = states[:n]
    k = len(states)
    return {
        "name": clean_name(effect.get("name"), "Custom scene"),
        "type": t,
        "brightness": _clamp(effect.get("brightness", 100), 1, 100),
        "states": states,
        # cumulative band ends, one band per colour (the device rejects a count that doesn't match the states)
        "segments": [max(0, min(n - 1, round((i + 1) * n / k) - 1)) for i in range(k)],
        "id": effect_id(f"seg:{effect.get('uid') or effect.get('name')}:{t}"),
    }


#: Id used for streamed frames. 32 hex characters: the strip silently ignores any other shape.
FRAME_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def frame_effect(colors: list, transition_ms: int = 0, brightness: int = 100, effect_id: str | None = None) -> dict:
    """A Static effect that paints each of the strip's zones its own colour.

    segments=[0] + expansion_strategy=1 with one colour per zone maps the
    sequence one-to-one onto the strip (measured by the tapoviz project).
    """
    seq = [hsb(c) for c in colors[:MAX_COLORS]]
    return {
        "name": "Tapo Studio",
        "type": "static",
        "custom": 1,
        "enable": 1,
        "brightness": _clamp(brightness, 1, 100),
        "display_colors": seq[:8],
        "segments": [0],
        "expansion_strategy": 1,
        "duration": 0,
        "transition": _clamp(transition_ms, 0, 65535),
        "sequence": seq,
        # A fixed id keeps the strip's effect list from filling up, and must differ from any
        # sequence-type id (see validate_lighting). Pass `effect_id` for firmware that ignores a re-used id.
        "id": effect_id or FRAME_ID,
    }


# ---- ways of painting the zones ------------------------------------------
# Older firmware paints with a static *lighting effect*. Newer firmware (1.4.x seen on an L930) accepts that
# command but never applies it; the newer Tapo app effects go through the separate *segment effect* ("scene")
# request. Which one a given strip accepts can only be found by trying and reading back, see Core.paint_test.
FRAME_VARIANTS = [
    ("lighting-default", "lighting", "Lighting effect (standard)"),
    ("lighting-bare", "lighting", "Lighting effect without timing fields"),
    ("lighting-indexed", "lighting", "Lighting effect listing every zone"),
    ("lighting-prefixed-id", "lighting", "Lighting effect with a TapoStrip-style id"),
    ("segment-none-indexed", "segment", "Scene-style effect, one colour per zone (newer firmware)"),
    ("segment-none-count", "segment", "Scene-style effect, single band"),
]
DEFAULT_PAINT_METHOD = "lighting-default"


def variant_kind(key: str) -> str:
    for k, kind, _ in FRAME_VARIANTS:
        if k == key:
            return kind
    return "lighting"


def prefixed_id() -> str:
    """An id shaped like the built-in ones ('TapoStrip_' + 22 letters/digits)."""
    import secrets
    import string
    return "TapoStrip_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(22))


def build_frame(method: str, colors: list, transition_ms: int = 0, brightness: int = 100,
                effect_id: str | None = None) -> dict:
    """The request body that paints each zone its own colour, for the given paint method.

    Lighting methods return a lighting-effect dict, segment methods a scene-effect dict."""
    seq = [hsb(c) for c in colors[:MAX_COLORS]]
    n = len(seq)
    if variant_kind(method) == "lighting":
        e = frame_effect(seq, transition_ms, brightness, effect_id)
        if method == "lighting-bare":
            e.pop("transition", None)
            e.pop("duration", None)
        elif method == "lighting-indexed":
            e["segments"] = list(range(n))
        elif method == "lighting-prefixed-id":
            e["id"] = effect_id or prefixed_id()
        return e
    # Verified on an L930-5 with firmware 1.4.3: `segments` are cumulative band-end indexes with one state per
    # band, so one colour per zone is [0, 1, ... n-1]; type "none" paints statically; a fixed id updates in place.
    segments = [n - 1] if method == "segment-none-count" else list(range(n))
    return {"name": "Tapo Studio", "type": "none", "brightness": _clamp(brightness, 1, 100),
            "states": [c + [0] for c in seq], "segments": segments, "id": effect_id or FRAME_ID}


def preset_lighting(name: str) -> dict | None:
    for p in LIGHTING_PRESETS:
        if p["name"].lower() == name.lower() or p["id"] == name:
            return json.loads(json.dumps(p))
    return None


def preset_segment(enum_or_name: str) -> dict | None:
    for p in SEGMENT_PRESETS:
        if enum_or_name in (p["enum"], p["name"]):
            return p
    return None


# ---- Studio ambient library -----------------------------------------------
# Effects the official app doesn't ship, built from the strip's own engine so
# they animate smoothly on-device with no PC streaming.

def _r(name, hue, sat, bri, init, bg, trans, fade, dur=0, seed=50, trange=None, segs=None):
    d = {"uid": f"studio-{name}", "name": name, "type": "random", "custom": 1, "brightness": 100,
         "hue_range": hue, "saturation_range": sat, "brightness_range": bri,
         "init_states": init, "backgrounds": bg, "transition": trans, "fadeoff": fade,
         "duration": dur, "random_seed": seed, "segments": segs or [0], "expansion_strategy": 1}
    if trange:
        d["transition_range"] = trange
    mid = (hue[0] + hue[1]) // 2
    d["display_colors"] = [[hue[0], sat[1], 100], [mid, sat[1], 100], [hue[1], sat[1], 100]] + bg[:1]
    return d


def _s(name, seq, spread, trans, direction=1, dur=0):
    return {"uid": f"studio-{name}", "name": name, "type": "sequence", "custom": 1, "brightness": 100,
            "sequence": seq, "spread": spread, "transition": trans, "direction": direction,
            "duration": dur, "repeat_times": 0, "segments": [0], "expansion_strategy": 1}


STUDIO_EFFECTS = [
    _r("Candle", [25, 35], [90, 100], [40, 90], [[30, 95, 70]], [[25, 100, 35]], 0, 400, trange=[120, 380]),
    _r("Fireplace", [5, 32], [95, 100], [35, 100], [[20, 100, 80]], [[10, 100, 30]], 0, 600, trange=[150, 600]),
    _r("Sparkle", [40, 55], [0, 25], [70, 100], [[45, 20, 100]], [[230, 70, 8]], 80, 500, dur=200, seed=7),
    _r("Fireflies", [60, 90], [70, 100], [60, 100], [[75, 90, 80]], [[130, 80, 3]], 400, 1500, dur=300, seed=11),
    _r("Starry night", [200, 240], [0, 30], [50, 100], [[220, 20, 90]], [[235, 90, 6]], 600, 2500, dur=400, seed=3),
    _r("Underwater", [185, 215], [70, 100], [30, 80], [[195, 90, 60]], [[205, 100, 25]], 1500, 2500, seed=21),
    _r("Cosmos", [250, 320], [60, 100], [30, 90], [[280, 80, 70]], [[260, 90, 12]], 1200, 3000, seed=42),
    _r("Neon city", [280, 340], [80, 100], [60, 100], [[300, 100, 90]], [[190, 100, 30]], 300, 900, dur=300, seed=9),
    _s("Ocean drift", [[200, 100, 70], [185, 90, 60], [220, 80, 40], [175, 70, 70]], 14, 2500, direction=3),
    _s("Lava lamp", [[10, 100, 90], [330, 90, 60], [35, 100, 85], [300, 80, 50]], 12, 3500),
    _s("Synthwave", [[300, 100, 100], [190, 100, 100], [270, 90, 90]], 10, 2000),
    _s("Northern lights", [[140, 100, 80], [170, 90, 60], [270, 70, 60], [120, 90, 40]], 16, 4000, direction=4),
    _s("Police", [[0, 100, 100], [0, 100, 100], [0, 0, 0], [240, 100, 100], [240, 100, 100], [0, 0, 0]], 1, 120, dur=150),
    _s("Candy stripes", [[330, 70, 100], [0, 0, 100]] * 4, 1, 400, dur=500),
]


# ---- colour helpers -------------------------------------------------------

def rgb_to_hsb(r: float, g: float, b: float) -> list[int]:
    """0-255 RGB -> device HSB."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return [int(round(h * 360)) % 361, int(round(s * 100)), int(round(v * 100))]


def hsb_to_rgb(c) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb((c[0] % 360) / 360, c[1] / 100, c[2] / 100)
    return int(r * 255), int(g * 255), int(b * 255)
