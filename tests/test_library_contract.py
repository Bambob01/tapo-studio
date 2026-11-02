"""Checks Tapo Studio against the REAL `tapo` library. Skipped when it isn't installed.

CI installs it, so an upgrade that renames something we rely on fails here
instead of at runtime on someone's lights.
"""

import asyncio
import inspect

import pytest

tapo = pytest.importorskip("tapo")

from tapo_studio import devices  # noqa: E402
from tapo_studio import effects as fx  # noqa: E402
from tapo_studio.driver import Strip, TapoBackend, preset_enum_name  # noqa: E402

R = tapo.requests

LIGHTING_BUILDERS = ["with_brightness", "with_is_custom", "with_display_colors", "with_enabled", "with_id", "with_name",
                     "with_type", "with_backgrounds", "with_brightness_range", "with_direction", "with_duration",
                     "with_expansion_strategy", "with_fade_off", "with_hue_range", "with_init_states",
                     "with_random_seed", "with_repeat_times", "with_run_time", "with_saturation_range",
                     "with_segment_length", "with_segments", "with_sequence", "with_spread", "with_transition",
                     "with_transition_range", "with_transition_sequence"]


def test_effect_classes_have_the_builders_we_call():
    for name in LIGHTING_BUILDERS:
        assert hasattr(R.LightingEffect, name), name
    for name in ("with_id", "with_segments", "with_states"):
        assert hasattr(R.SegmentEffect, name), name


def test_enums_and_presets_exist():
    for t in ("Sequence", "Random", "Pulse", "Static"):
        assert hasattr(R.LightingEffectType, t)
    for t in fx.SEGMENT_TYPES:
        assert hasattr(R.SegmentEffectType, t.capitalize()), t
    for p in fx.LIGHTING_PRESETS:
        assert hasattr(R.LightingEffectPreset, preset_enum_name(p["name"])), p["name"]
    for p in fx.SEGMENT_PRESETS:
        assert hasattr(R.SegmentEffectPreset, p["enum"]), p["enum"]


def test_client_has_a_factory_for_every_model_we_support():
    for model, (_, factory) in devices.MODELS.items():
        assert hasattr(tapo.ApiClient, factory), model
    assert hasattr(tapo.ApiClient, "discover_devices_raw")
    assert inspect.iscoroutinefunction(getattr(tapo.ApiClient, "l930"))


def test_handlers_have_the_methods_we_call():
    for cls in (tapo.ColorLightHandler, tapo.RgbLightStripHandler, tapo.RgbicLightStripHandler):
        for m in ("set", "set_brightness", "on", "off", "get_device_info_json", "device_reboot"):
            assert hasattr(cls, m), (cls.__name__, m)
    assert not hasattr(tapo.LightHandler, "set")
    for m in ("set_lighting_effect", "set_segment_effect"):
        assert hasattr(tapo.RgbicLightStripHandler, m)


def test_our_effects_convert_to_library_objects_and_back():
    b = TapoBackend()
    for e in fx.LIGHTING_PRESETS + fx.STUDIO_EFFECTS:
        valid = fx.validate_lighting(e)
        wire = b._lighting(valid).to_dict()
        for key, value in valid.items():
            assert wire.get(key) == value, (e["name"], key, wire.get(key), value)
