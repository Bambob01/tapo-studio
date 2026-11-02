"""Colour maths: Oklab blending, gamma, palettes.

Blending in Oklab keeps gradients bright and even. A plain RGB blue-to-yellow
fade goes through muddy grey, while an Oklab fade stays vivid.
"""

from __future__ import annotations

import colorsys
import math

import numpy as np

# ---- sRGB <-> Oklab ---------------------------------------------------------

def _lin(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _delin(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def rgb_to_oklab(rgb):
    r, g, b = _lin(np.asarray(rgb, float) / 255).T
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    return np.stack([
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    ], axis=-1)


def oklab_to_rgb(lab):
    L, a, b = np.asarray(lab, float).T
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = np.stack([
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    ], axis=-1)
    return np.rint(_delin(rgb) * 255).astype(int)


# ---- device HSB <-> RGB -----------------------------------------------------

def hsb_to_rgb(c):
    r, g, b = colorsys.hsv_to_rgb((c[0] % 360) / 360, c[1] / 100, c[2] / 100)
    return [round(r * 255), round(g * 255), round(b * 255)]


def rgb_to_hsb(rgb):
    h, s, v = colorsys.rgb_to_hsv(*(max(0, min(255, x)) / 255 for x in rgb))
    return [round(h * 360) % 360, round(s * 100), round(v * 100)]


def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def gradient(stops: list, n: int, positions: list | None = None, cyclic: bool = False) -> list[list[int]]:
    """n RGB colours blended through the given RGB stops in Oklab."""
    stops = [list(s) for s in stops] or [[255, 255, 255]]
    if len(stops) == 1:
        return [stops[0][:] for _ in range(n)]
    pts = list(stops) + ([stops[0]] if cyclic else [])
    k = len(pts)
    pos = positions[:] if positions and len(positions) == len(stops) else [i / (len(stops) - (0 if cyclic else 1)) for i in range(len(stops))]
    if cyclic:
        pos = pos + [1.0]
    lab = rgb_to_oklab(np.array(pts))
    out = []
    for i in range(n):
        x = i / (n if cyclic else max(1, n - 1))
        j = 0
        while j < k - 2 and x > pos[j + 1]:
            j += 1
        span = max(1e-9, pos[j + 1] - pos[j])
        f = min(1.0, max(0.0, (x - pos[j]) / span))
        out.append(lab[j] * (1 - f) + lab[j + 1] * f)
    return [list(map(int, c)) for c in oklab_to_rgb(np.array(out))]


def sample(palette_rgb: list, x: float) -> list[int]:
    """Colour at position x (0-1, wraps) of a cyclic palette."""
    n = len(palette_rgb)
    if n == 1:
        return list(palette_rgb[0])
    x = (x % 1.0) * n
    i = int(x)
    f = x - i
    a, b = rgb_to_oklab(np.array([palette_rgb[i % n], palette_rgb[(i + 1) % n]]))
    return list(map(int, oklab_to_rgb((a * (1 - f) + b * f)[None])[0]))


def apply_gamma(v: float, gamma: float = 2.1) -> float:
    """Perceptual brightness: makes quiet/dim parts look properly dim."""
    return math.pow(max(0.0, min(1.0, v)), gamma)


# ---- palette library ------------------------------------------------------

PALETTES: dict[str, list[str]] = {
    "Sunset": ["#ff5e3a", "#ff2a68", "#a234ff", "#ff9500"],
    "Ocean": ["#003cff", "#00b3ff", "#00ffd5", "#0061a8"],
    "Forest": ["#0b6e1f", "#7ed957", "#2e8b57", "#c4e86b"],
    "Lava": ["#ff2200", "#ff7b00", "#ffb300", "#8a0000"],
    "Aurora": ["#00ff9c", "#00c3ff", "#7a00ff", "#00ffb3"],
    "Synthwave": ["#ff00c8", "#00e5ff", "#7b2dff", "#ff6ad5"],
    "Candy": ["#ff7eb6", "#7afcff", "#feff9c", "#b28dff"],
    "Ice": ["#e0f7ff", "#7fd4ff", "#2f7bff", "#ffffff"],
    "Fire": ["#ff3d00", "#ff8a00", "#ffd000", "#ff1a00"],
    "Rainbow": ["#ff0000", "#ffaa00", "#ffff00", "#00ff00", "#00aaff", "#7a00ff"],
    "Cyberpunk": ["#fcee0a", "#00f0ff", "#ff003c", "#7a00ff"],
    "Cozy": ["#ff9a3c", "#ffcf7a", "#ff6b35", "#ffe8c2"],
    "Mint": ["#3cffb0", "#00d4a0", "#b5ffe1", "#00a884"],
    "Royal": ["#3a0ca3", "#7209b7", "#f72585", "#4361ee"],
    "Christmas": ["#ff0000", "#00b140", "#ffffff", "#ffcc00"],
    "Halloween": ["#ff6a00", "#7a00ff", "#00ff2a", "#ff6a00"],
}
