"""Which Tapo lights Tapo Studio knows about and what each one can do.

The `tapo` library groups the lights into four handler classes. What the
library exposes for each group decides what we can offer:

=============  =======================  ==========================================
Family         Models                   Capabilities
=============  =======================  ==========================================
rgbic_strip    L920, L930               everything: per-zone colour, effects, scenes
rgb_strip      L900                     colour, white, brightness
color_bulb     L430, L530, L535, L630   colour, white, brightness
white_bulb     L510, L520, L610         brightness only
=============  =======================  ==========================================

Capability names used throughout the app:

* ``power``, ``brightness`` - every light
* ``color``                 - hue and saturation
* ``white``                 - colour temperature (2500-6500 K)
* ``zones``                 - individually addressable zones (paint, live frames)
* ``effects``               - lighting effects and scene animations
"""

from __future__ import annotations

import re
from typing import NamedTuple


class Family(NamedTuple):
    key: str
    label: str
    caps: frozenset
    zones: int          # default number of zones


_BASIC = frozenset({"power", "brightness"})

FAMILIES: dict[str, Family] = {
    "rgbic_strip": Family("rgbic_strip", "RGBIC light strip",
                          _BASIC | {"color", "white", "zones", "effects"}, 50),
    "rgb_strip": Family("rgb_strip", "RGB light strip", _BASIC | {"color", "white"}, 1),
    "color_bulb": Family("color_bulb", "Colour bulb", _BASIC | {"color", "white"}, 1),
    "white_bulb": Family("white_bulb", "White bulb", _BASIC, 1),
}

# base model -> (family, name of the ApiClient method that returns its handler)
MODELS: dict[str, tuple[str, str]] = {
    "L930": ("rgbic_strip", "l930"), "L920": ("rgbic_strip", "l920"),
    "L900": ("rgb_strip", "l900"),
    "L430": ("color_bulb", "l430"), "L530": ("color_bulb", "l530"),
    "L535": ("color_bulb", "l535"), "L630": ("color_bulb", "l630"),
    "L510": ("white_bulb", "l510"), "L520": ("white_bulb", "l520"), "L610": ("white_bulb", "l610"),
}

CAP_LABELS = {
    "zones": "individually addressable zones",
    "effects": "lighting effects",
    "color": "colour",
    "white": "white light",
}


def base_model(model) -> str | None:
    """'L930-5(EU)' -> 'L930', 'L430C' -> 'L430', anything else -> None."""
    m = re.match(r"\s*(L\d{3})", str(model or "").upper())
    return m.group(1) if m and m.group(1) in MODELS else None


def family_for(model) -> Family | None:
    b = base_model(model)
    return FAMILIES[MODELS[b][0]] if b else None


def factory_name(model) -> str | None:
    b = base_model(model)
    return MODELS[b][1] if b else None


def caps_for(model) -> frozenset:
    """Capabilities of a model; unknown models get the safe minimum."""
    fam = family_for(model)
    return fam.caps if fam else _BASIC


def catalog() -> list[dict]:
    """Model list for the UI."""
    out = []
    for model, (fam_key, _) in MODELS.items():
        fam = FAMILIES[fam_key]
        out.append({"model": model, "family": fam.key, "label": fam.label,
                    "caps": sorted(fam.caps), "zones": fam.zones})
    return out
