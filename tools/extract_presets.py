#!/usr/bin/env python3
"""Regenerate the bundled preset tables from the `tapo` library's Rust source.

The Python bindings of `tapo` (https://github.com/mihai-dinculescu/tapo, MIT
licence) don't expose the parameters of the strip's built-in effects, but the
Rust source does. This script reads that source and writes:

    tapo_studio/data/lighting_presets.json   17 lighting effects (full parameters)
    tapo_studio/data/segment_presets.json    55 scene animations (id, name, colours)

Usage:
    git clone --depth 1 https://github.com/mihai-dinculescu/tapo
    python tools/extract_presets.py ./tapo

The output is deterministic, so running it again produces identical files.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SRC = Path("tapo/src/requests/set_device_info")
OUT = Path(__file__).resolve().parent.parent / "tapo_studio" / "data"

# Builder method on the Rust side -> JSON key the strip understands.
BUILDERS = {
    "with_id": "id", "with_direction": "direction", "with_duration": "duration",
    "with_expansion_strategy": "expansion_strategy", "with_repeat_times": "repeat_times",
    "with_segments": "segments", "with_sequence": "sequence", "with_spread": "spread",
    "with_transition": "transition", "with_backgrounds": "backgrounds",
    "with_brightness_range": "brightness_range", "with_init_states": "init_states",
    "with_fade_off": "fadeoff", "with_hue_range": "hue_range", "with_random_seed": "random_seed",
    "with_saturation_range": "saturation_range", "with_transition_range": "transition_range",
    "with_transition_sequence": "trans_sequence", "with_run_time": "run_time",
    "with_segment_length": "segment_length",
}


def rust_value(text: str):
    """Rust literal (numbers, strings, arrays, vec![...]) -> Python value."""
    text = re.sub(r"vec!\s*", "", text)
    text = re.sub(r",\s*([\]\)])", r"\1", text)      # trailing commas
    return json.loads(text)


def split_args(s: str) -> list[str]:
    """Split a call's argument text on top-level commas."""
    out, depth, cur, in_str = [], 0, [], False
    for ch in s:
        if ch == '"':
            in_str = not in_str
        if not in_str:
            if ch in "[(":
                depth += 1
            elif ch in "])":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def balanced(s: str, start: int) -> tuple[str, int]:
    """Text inside the parentheses opening at s[start]; returns (inner, end_index)."""
    depth, i, in_str = 0, start, False
    while i < len(s):
        ch = s[i]
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return s[start + 1:i], i
        i += 1
    raise ValueError("unbalanced parentheses")


def lighting(src: str) -> list[dict]:
    body = src[src.index("impl LightingEffectPreset"):]
    out = []
    for m in re.finditer(r"fn (\w+)\(self\) -> LightingEffect \{\s*LightingEffect::new\(", body):
        inner, end = balanced(body, m.end() - 1)
        name, typ, custom, enabled, bri, display = split_args(inner)
        e = {
            "custom": int(custom == "true"), "id": None, "brightness": int(bri),
            "name": rust_value(name), "enable": int(enabled == "true"),
            "type": re.search(r"::(\w+)$", typ).group(1).lower(),
            "display_colors": rust_value(display),
        }
        pos = end + 1
        while True:
            c = re.match(r"\s*\.(\w+)\(", body[pos:])
            if not c:
                break
            arg, close = balanced(body, pos + c.end() - 1)
            if c.group(1) in BUILDERS:
                e[BUILDERS[c.group(1)]] = rust_value(arg)
            else:
                raise SystemExit(f"unknown builder {c.group(1)} in {e['name']}")
            pos = close + 1
        out.append(e)
    return out


def segments(src: str) -> list[dict]:
    names = dict((fn, enum) for enum, fn in re.findall(r"SegmentEffectPreset::(\w+) => val\.(\w+)\(\)", src))
    out = []
    for fn, body in re.findall(r"fn (\w+)\(self\) -> SegmentEffect \{(.*?)\n    \}", src, re.S):
        if fn not in names:
            continue
        name = re.search(r'preset\("(\w+)"\)', body).group(1)
        out.append({
            "enum": names[fn], "name": name, "label": name.replace("_", " ").title(),
            "brightness": int(re.search(r"with_brightness\((\d+)\)", body).group(1)),
            "colors": [list(map(int, c)) for c in re.findall(r"\[(\d+), (\d+), (\d+), (\d+)\]", body)],
        })
    return out


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "tapo")
    L = lighting((root / SRC / "lighting_effect.rs").read_text())
    S = segments((root / SRC / "segment_effect.rs").read_text())
    assert len(L) == 17 and len(S) == 55, f"expected 17 and 55 presets, found {len(L)} and {len(S)}"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "lighting_presets.json").write_text(json.dumps(L, separators=(",", ":")) + "\n")
    (OUT / "segment_presets.json").write_text(json.dumps(S, separators=(",", ":")) + "\n")
    print(f"wrote {len(L)} lighting and {len(S)} segment presets to {OUT}")


if __name__ == "__main__":
    main()
