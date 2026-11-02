"""Pure-logic tests: effect validation, colour maths, device families."""

import json
import re
import time

import pytest

from tapo_studio import color as C
from tapo_studio import devices
from tapo_studio import effects as fx


# ---- bundled data --------------------------------------------------------

def test_preset_tables_complete():
    assert len(fx.LIGHTING_PRESETS) == 17
    assert len(fx.SEGMENT_PRESETS) == 55
    assert len({p["id"] for p in fx.LIGHTING_PRESETS}) == 17


def test_every_preset_and_studio_effect_validates():
    for e in fx.LIGHTING_PRESETS + fx.STUDIO_EFFECTS:
        out = fx.validate_lighting(e)
        assert re.fullmatch(r"[0-9a-f]{32}", out["id"]), e["name"]   # the strip ignores any other id shape
        assert out["type"] in fx.LIGHTING_TYPES
        assert len(out["display_colors"]) <= 8


def test_candy_cane_and_lightning_keep_their_zone_lists():
    # Regression: these presets target specific zones (values come from the MIT-licensed tapo source).
    by = {p["name"]: p for p in fx.LIGHTING_PRESETS}
    assert by["Candy Cane"]["segments"] == list(range(16))
    assert by["Lightning"]["segments"][:3] == [7, 20, 23]


# ---- validation ----------------------------------------------------------

def test_values_are_clamped():
    e = fx.validate_lighting({"type": "sequence", "brightness": 500, "transition": 10**9, "spread": -5,
                              "sequence": [[999, 500, -3]]})
    assert e["brightness"] == 100 and e["transition"] == 65535 and e["spread"] == 0
    assert e["sequence"] == [[360, 100, 0]]


@pytest.mark.parametrize("bad", [
    {"type": "nonsense", "sequence": [[0, 0, 0]]},
    {"type": "sequence"},                                         # needs colours
    {"type": "sequence", "sequence": "red"},
    {"type": "sequence", "sequence": [[1, 2]]},
    {"type": "random", "hue_range": 5},
    {"type": "sequence", "sequence": [[0, 0, 0]], "segments": "all"},
    "not a dict",
])
def test_bad_effects_are_rejected(bad):
    with pytest.raises(fx.EffectError):
        fx.validate_lighting(bad)


def test_oversized_effects_rejected():
    big = {"type": "sequence", "sequence": [[0, 0, 0]] * 100, "backgrounds": [[0, 0, 0]] * 50}
    with pytest.raises(fx.EffectError):
        fx.validate_lighting(big)


def test_names_are_cleaned():
    e = fx.validate_lighting({"name": "a\x00‮b\x07c", "type": "static", "sequence": [[0, 0, 0]]})
    assert e["name"] == "abc"


def test_effect_id_depends_on_type():
    a = fx.validate_lighting({"uid": "x", "type": "static", "sequence": [[0, 0, 0]]})
    b = fx.validate_lighting({"uid": "x", "type": "sequence", "sequence": [[0, 0, 0]]})
    assert a["id"] != b["id"]    # the strip won't change an existing id's type


def test_frame_effect_paints_one_colour_per_zone():
    e = fx.frame_effect([[i, 100, 100] for i in range(50)], 300)
    assert len(e["sequence"]) == 50 and e["type"] == "static" and e["transition"] == 300
    assert re.fullmatch(r"[0-9a-f]{32}", e["id"])


def test_segment_effect_validation():
    e = fx.validate_segment_effect({"type": "breathe", "states": [[10, 20, 30, 0]]}, 50)
    assert e["segments"] == [49] and e["states"] == [[10, 20, 30, 0]]
    # one band per colour: the device rejects a zone list that doesn't match the colours
    two = fx.validate_segment_effect({"type": "breathe", "states": [[10, 20, 30, 0], [1, 2, 3, 0]]}, 50)
    assert two["segments"] == [24, 49]
    five = fx.validate_segment_effect({"type": "chasing", "states": [[i, 50, 50, 0] for i in range(5)]}, 50)
    assert five["segments"] == [9, 19, 29, 39, 49]
    many = fx.validate_segment_effect({"type": "chasing", "states": [[i, 50, 50, 0] for i in range(80)]}, 50)
    assert len(many["states"]) == 50 and many["segments"] == list(range(50))
    with pytest.raises(fx.EffectError):
        fx.validate_segment_effect({"type": "breathe", "states": []}, 50)
    with pytest.raises(fx.EffectError):
        fx.validate_segment_effect({"type": "bogus", "states": [[0, 0, 0, 0]]}, 50)


# ---- colour --------------------------------------------------------------

def test_oklab_roundtrip():
    import numpy as np
    rgb = np.array([[255, 0, 0], [10, 200, 90], [0, 0, 0], [255, 255, 255]])
    back = C.oklab_to_rgb(C.rgb_to_oklab(rgb))
    assert np.abs(back - rgb).max() <= 1


def test_gradient_hits_its_stops():
    g = C.gradient([[255, 0, 0], [0, 0, 255]], 11)
    assert g[0] == [255, 0, 0] and g[-1] == [0, 0, 255] and len(g) == 11


def test_gradient_stays_vivid_in_the_middle():
    # A plain RGB blend of blue and yellow goes to grey; Oklab keeps more colour.
    mid = C.gradient([[0, 0, 255], [255, 255, 0]], 3)[1]
    assert max(mid) - min(mid) > 60


def test_hsb_rgb_roundtrip():
    for hsb in ([0, 100, 100], [120, 50, 80], [240, 100, 30], [300, 20, 100]):
        back = C.rgb_to_hsb(C.hsb_to_rgb(hsb))
        assert abs(back[0] - hsb[0]) <= 2 and abs(back[1] - hsb[1]) <= 2 and abs(back[2] - hsb[2]) <= 2


def test_daylight_curve():
    from tapo_studio.live import daylight_curve
    noon = daylight_curve(time.strptime("12:00", "%H:%M"), "07:00", "23:00")
    night = daylight_curve(time.strptime("03:00", "%H:%M"), "07:00", "23:00")
    assert noon[0] > 5000 and noon[1] > 80
    assert night == (2500, 10)


# ---- device families -----------------------------------------------------

@pytest.mark.parametrize("model,family", [
    ("L930", "rgbic_strip"), ("L930-5(EU)", "rgbic_strip"), ("l920", "rgbic_strip"),
    ("L900-10", "rgb_strip"), ("L430C", "color_bulb"), ("L530E", "color_bulb"), ("L535", "color_bulb"),
    ("L630", "color_bulb"), ("L510B", "white_bulb"), ("L520E", "white_bulb"), ("L610", "white_bulb"),
])
def test_known_models(model, family):
    assert devices.family_for(model).key == family


@pytest.mark.parametrize("model", ["P110", "H100", "", None, "L999", "garbage"])
def test_unknown_models(model):
    assert devices.base_model(model) is None
    assert devices.caps_for(model) == {"power", "brightness"}


def test_only_rgbic_strips_have_zones_and_effects():
    for model, (fam, _) in devices.MODELS.items():
        caps = devices.FAMILIES[fam].caps
        is_rgbic = fam == "rgbic_strip"
        assert ("zones" in caps) == is_rgbic
        assert ("effects" in caps) == is_rgbic


def test_catalog_lists_every_model():
    assert {m["model"] for m in devices.catalog()} == set(devices.MODELS)


# ---- paint methods -------------------------------------------------------------

def test_paint_methods_build_the_right_requests():
    cols = [[i * 7 % 360, 100, 90] for i in range(50)]
    d = fx.build_frame("lighting-default", cols, 300)
    assert d["type"] == "static" and d["transition"] == 300 and d["segments"] == [0] and len(d["sequence"]) == 50
    b = fx.build_frame("lighting-bare", cols, 300)
    assert "transition" not in b and "duration" not in b and b["segments"] == [0]
    assert fx.build_frame("lighting-indexed", cols)["segments"] == list(range(50))
    assert re.fullmatch(r"TapoStrip_[A-Za-z0-9]{22}", fx.build_frame("lighting-prefixed-id", cols)["id"])
    for key, segs in (("segment-none-indexed", list(range(50))), ("segment-none-count", [49])):
        e = fx.build_frame(key, cols, 300, 80)
        assert e["type"] == "none" and e["segments"] == segs and e["brightness"] == 80
        assert len(e["states"]) == 50 and all(len(c) == 4 and c[3] == 0 for c in e["states"])
        assert e["id"] == fx.FRAME_ID                              # a fixed id updates the light in place
    assert fx.build_frame("segment-none-indexed", cols)["segments"][-1] == 49
    assert fx.variant_kind("segment-none-count") == "segment" and fx.variant_kind("lighting-bare") == "lighting"
    assert fx.variant_kind("nonsense") == "lighting"
