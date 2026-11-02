"""The real-hardware driver (TapoBackend) run against a strict fake of the `tapo` library.

This covers the code path your lights use: choosing the right handler per model,
never sending credentials to the wrong device, and converting our effect
dictionaries into the library's objects.
"""

import asyncio

import fake_tapo
import pytest

from tapo_studio import effects as fx
from tapo_studio.driver import Strip, StripError, TapoBackend, preset_enum_name


@pytest.fixture
def backend(monkeypatch):
    fake_tapo.install(monkeypatch)
    b = TapoBackend()
    b.set_credentials("me@example.com", "pw")
    return b


def run(coro):
    return asyncio.run(coro)


def strip(ip="192.168.1.50", model="L930", mac=None):
    s = Strip("id1", ip, "Test", 50 if model.startswith("L9") else 1, model)
    s.mac = mac
    return s


# ---- discovery never uses your password ------------------------------------

def test_search_sends_no_login(backend):
    fake_tapo.announce("192.168.1.50", "L930-5(EU)", "AA:BB:CC:DD:EE:50")
    fake_tapo.announce("192.168.1.51", "L530E", "AA:BB:CC:DD:EE:51")
    fake_tapo.announce("192.168.1.60", "P110", "AA:BB:CC:DD:EE:60")
    found = run(backend.discover("192.168.1.255", 2))
    assert {f["ip"]: f["supported"] for f in found} == {"192.168.1.50": True, "192.168.1.51": True, "192.168.1.60": False}
    assert fake_tapo.LOGINS == []
    assert next(f for f in found if f["ip"].endswith(".51"))["mac"] == "AABBCCDDEE51"


# ---- handler per model -----------------------------------------------------

@pytest.mark.parametrize("model,handler", [
    ("L930", "RgbicLightStripHandler"), ("L920", "RgbicLightStripHandler"), ("L900", "RgbLightStripHandler"),
    ("L530", "ColorLightHandler"), ("L535", "ColorLightHandler"), ("L630", "ColorLightHandler"),
    ("L430", "ColorLightHandler"), ("L510", "LightHandler"), ("L520", "LightHandler"), ("L610", "LightHandler"),
])
def test_each_model_gets_its_own_handler(backend, model, handler):
    fake_tapo.announce("192.168.1.50", model)
    s = strip(model=model)
    run(backend.connect(s))
    assert s.connected and type(backend.h(s)).__name__ == handler


def test_device_model_overrides_what_we_had_saved(backend):
    fake_tapo.announce("192.168.1.50", "L530E")       # the user said L930, the device says L530
    s = strip(model="L930")
    run(backend.connect(s))
    assert s.model == "L530" and type(backend.h(s)).__name__ == "ColorLightHandler"


# ---- credentials only go to the right device ------------------------------

def test_different_device_at_saved_ip_gets_no_login(backend):
    fake_tapo.announce("192.168.1.50", "L930", mac="11:11:11:11:11:11")
    s = strip(mac="AABBCCDDEEFF")                      # we saved a different hardware address
    with pytest.raises(StripError, match="different device"):
        run(backend.connect(s))
    assert fake_tapo.LOGINS == []


def test_unsupported_device_gets_no_login(backend):
    fake_tapo.announce("192.168.1.50", "P110")
    with pytest.raises(StripError, match="doesn't support"):
        run(backend.connect(strip()))
    assert fake_tapo.LOGINS == []


def test_old_insecure_login_method_is_refused(backend):
    fake_tapo.announce("192.168.1.50", "L930", encrypt="AES")
    with pytest.raises(StripError, match="insecure"):
        run(backend.connect(strip()))
    assert fake_tapo.LOGINS == []


def test_hardware_ids_are_remembered_after_first_connect(backend):
    fake_tapo.announce("192.168.1.50", "L930", mac="AA:BB:CC:DD:EE:50")
    fake_tapo.INFO_MAC["192.168.1.50"] = "aa-bb-cc-dd-ee-50"
    s = strip()
    run(backend.connect(s))
    assert s.mac == "AABBCCDDEE50" and s.info_mac == "AABBCCDDEE50"


def test_discovery_and_device_report_may_disagree_without_locking_you_out(backend):
    # Regression: the app once compared the discovery ID with the device's own report and refused to connect
    # whenever they weren't identical, which would lock a real user out of their own light.
    fake_tapo.announce("192.168.1.50", "L930", mac="AA:BB:CC:DD:EE:50")
    fake_tapo.INFO_MAC["192.168.1.50"] = "40-ED-00-12-34-56"          # a completely different value
    s = strip(mac="AABBCCDDEE50")                                       # as saved from discovery
    run(backend.connect(s))
    assert s.connected and s.info_mac == "40ED00123456"


def test_a_swapped_light_is_caught_by_its_own_report(backend):
    fake_tapo.announce("192.168.1.50", "L930")                          # no discovery ID to compare
    fake_tapo.INFO_MAC["192.168.1.50"] = "AA-AA-AA-AA-AA-AA"
    s = strip()
    s.info_mac = "BBBBBBBBBBBB"                                         # what we saved last time
    with pytest.raises(StripError, match="hardware ID changed"):
        run(backend.connect(s))
    assert not s.connected


def test_first_manual_connect_remembers_the_announced_id(backend):
    fake_tapo.announce("192.168.1.50", "L930", mac="AA:BB:CC:DD:EE:50")
    s = strip()                                                         # added by IP, never discovered
    run(backend.connect(s))
    assert s.mac == "AABBCCDDEE50"


# ---- effects are converted faithfully -------------------------------------

def sent_effect(backend, s, effect):
    run(backend.lighting(s, effect))
    return backend.h(s).calls[-1]


def test_every_bundled_effect_is_sent_exactly_as_validated(backend):
    fake_tapo.announce("192.168.1.50", "L930")
    s = strip()
    run(backend.connect(s))
    for e in fx.LIGHTING_PRESETS + fx.STUDIO_EFFECTS:
        valid = fx.validate_lighting(e)
        call = sent_effect(backend, s, valid)
        assert call[0] == "set_lighting_effect"
        assert call[1] == valid, e["name"]           # wire names (fadeoff, trans_sequence, custom, enable) all line up


def test_frame_effects_are_static_with_one_colour_per_zone(backend):
    fake_tapo.announce("192.168.1.50", "L930")
    s = strip()
    run(backend.connect(s))
    call = sent_effect(backend, s, fx.frame_effect([[i * 7 % 360, 100, 90] for i in range(50)], 250))
    wire = call[1]
    assert wire["type"] == "static" and len(wire["sequence"]) == 50 and wire["transition"] == 250 and wire["segments"] == [0]


def test_presets_resolve_to_library_enums(backend):
    fake_tapo.announce("192.168.1.50", "L930")
    s = strip()
    run(backend.connect(s))
    for p in fx.LIGHTING_PRESETS:
        run(backend.lighting_preset(s, p["name"]))
        assert backend.h(s).calls[-1] == ("set_lighting_effect", preset_enum_name(p["name"]))
    for p in fx.SEGMENT_PRESETS:
        run(backend.segment_preset(s, p["enum"]))
        assert backend.h(s).calls[-1] == ("set_segment_effect", p["enum"])
    with pytest.raises(StripError):
        run(backend.lighting_preset(s, "No such effect"))


def test_custom_scene_animation(backend):
    fake_tapo.announce("192.168.1.50", "L930")
    s = strip()
    run(backend.connect(s))
    e = fx.validate_segment_effect({"name": "Mine", "type": "breathe", "brightness": 70,
                                    "states": [[10, 20, 30, 0], [200, 90, 80, 0]]}, 50)
    run(backend.segment_custom(s, e))
    wire = backend.h(s).calls[-1][1]
    assert wire["type"] == "breathe" and wire["states"] == e["states"] and wire["brightness"] == 70
    assert wire["segments"] == [24, 49]               # one band per colour


# ---- colour, white, brightness on the different families ------------------

def test_colour_white_brightness_on_a_bulb(backend):
    fake_tapo.announce("192.168.1.51", "L530")
    s = strip("192.168.1.51", "L530")
    run(backend.connect(s))
    run(backend.color(s, 200, 80, 60))
    run(backend.white(s, 2700, 40))
    run(backend.brightness(s, 25))
    run(backend.power(s, False))
    assert backend.h(s).calls == [
        ("set", {"on": True, "hue": 200, "saturation": 80, "brightness": 60}),
        ("set", {"on": True, "color_temp": 2700, "brightness": 40}),
        ("set_brightness", 25), ("off",)]


def test_white_bulb_only_takes_brightness(backend):
    fake_tapo.announce("192.168.1.52", "L510")
    s = strip("192.168.1.52", "L510")
    run(backend.connect(s))
    run(backend.brightness(s, 0))                      # never below 1
    assert backend.h(s).calls == [("set_brightness", 1)]
    with pytest.raises(AttributeError):                # the library has no set() for these, so we must never call it
        backend.h(s).set()


def test_refresh_and_reboot(backend):
    fake_tapo.announce("192.168.1.50", "L930")
    s = strip()
    run(backend.connect(s))
    assert s.on and s.brightness == 80 and s.info["fw_ver"] == "1.0"
    run(backend.reboot(s))
    assert backend.h(s).calls[-1] == ("reboot", 3)


def test_missing_credentials_message():
    from tapo_studio.driver import TapoBackend as TB
    import fake_tapo as ft
    mp = pytest.MonkeyPatch()
    try:
        ft.install(mp)
        b = TB()                                       # no set_credentials
        with pytest.raises(StripError, match="email and password"):
            run(b.connect(strip()))
    finally:
        mp.undo()


# ---- the whole app on the real driver (against the fake library) ----------------

def test_paint_is_confirmed_retried_and_reported_through_the_real_driver(isolated_store, monkeypatch):
    from tapo_studio.core import Core
    fake_tapo.install(monkeypatch)
    fake_tapo.announce("192.168.1.50", "L930-5(EU)", "AA:BB:CC:DD:EE:50")

    async def go():
        core = Core(mock=False)
        await core.start()
        await core.set_credentials("me@example.com", "pw")
        await core.add_device("192.168.1.50", "Strip", None, "L930")
        s = next(iter(core.strips.values()))
        assert s.connected and core.backend.name == "tapo"

        r = await core.action(None, "paint", {"colors": [[0, 100, 100]]})
        assert r["warnings"] == [] and s.apply_mode == "fixed"
        wire = fake_tapo.HANDLERS["192.168.1.50"].calls[-1][1]
        assert wire["id"] == fx.FRAME_ID and len(wire["sequence"]) == 50

        fake_tapo.IGNORE_IDS.add(fx.FRAME_ID)             # firmware that ignores a re-used id
        r = await core.action(None, "paint", {"colors": [[120, 100, 100]]})
        assert r["warnings"] == [] and s.apply_mode == "fresh"
        assert fake_tapo.HANDLERS["192.168.1.50"].calls[-1][1]["id"] != fx.FRAME_ID

        fake_tapo.IGNORE_ALL[0] = True                    # firmware that shows nothing at all
        r = await core.action(None, "paint", {"colors": [[240, 100, 100]]})
        assert "not showing it" in r["warnings"][0]

        report = await core.diagnose()
        assert "firmware: 1.0" in report and "me@example.com" not in report and "AABBCC" not in report
    run(go())


def test_clearing_first_and_explaining_overrides_through_the_real_driver(isolated_store, monkeypatch):
    from tapo_studio.core import Core
    fake_tapo.install(monkeypatch)
    fake_tapo.announce("192.168.1.50", "L930-5(EU)", "AA:BB:CC:DD:EE:50")

    async def go():
        core = Core(mock=False)
        await core.start()
        await core.set_credentials("me@example.com", "pw")
        await core.add_device("192.168.1.50", "Strip", None, "L930")
        s = next(iter(core.strips.values()))
        h = fake_tapo.HANDLERS["192.168.1.50"]
        h.effect = {"enable": 1, "id": "TapoStrip_OLD", "name": "Rainbow"}      # something else is playing
        fake_tapo.NEEDS_CLEAR[0] = True
        r = await core.action(None, "paint", {"colors": [[0, 100, 100]]})
        assert r["warnings"] == [] and s.apply_mode == "clear"
        assert [c[0] for c in h.calls[-2:]] == ["set", "set_lighting_effect"]       # colour first, then the pattern

        fake_tapo.IGNORE_ALL[0] = True
        fake_tapo.SEGMENT_ON[0] = True
        fake_tapo.MUSIC_SYNC_ON[0] = True
        r = await core.action(None, "paint", {"colors": [[240, 100, 100]]})
        assert "scene animation is also active" in r["warnings"][0] and "Music sync" in r["warnings"][0]
        assert "phone music sync: True" in await core.diagnose()
    run(go())


def test_newer_firmware_on_the_real_driver_heals_and_follows_the_devices_rules(isolated_store, monkeypatch):
    from tapo_studio.core import Core
    fake_tapo.install(monkeypatch)
    fake_tapo.announce("192.168.1.50", "L930-5(EU)", "AA:BB:CC:DD:EE:50")
    fake_tapo.CUSTOM_LIGHTING_IGNORED[0] = True                        # firmware 1.4.x: accepts, never applies

    async def go():
        core = Core(mock=False)
        await core.start()
        await core.set_credentials("me@example.com", "pw")
        await core.add_device("192.168.1.50", "Strip", None, "L930")
        s = next(iter(core.strips.values()))
        h = fake_tapo.HANDLERS["192.168.1.50"]

        r = await core.action(None, "paint", {"colors": [[0, 100, 100], [240, 100, 100]]})
        assert r["warnings"] == [] and s.paint_method == "segment-none-indexed"
        name, wire = h.calls[-1]
        assert name == "set_segment_effect" and wire["type"] == "none"
        assert wire["segments"] == list(range(50)) and len(wire["states"]) == 50
        assert wire["states"][0] == [0, 100, 100, 0] and wire["states"][-1] == [240, 100, 100, 0]
        assert len(wire["display_colors"]) <= 4                         # the device answers -1008 to more than 4

        results = [await core.paint_test(s.id, i) for i in range(len(fx.FRAME_VARIANTS) + 1)]
        assert results[0]["accepted"] and [r["key"] for r in results[1:] if r["accepted"]] == ["segment-none-indexed"]
        assert "-1008" in next(r for r in results if r["key"] == "segment-none-count")["error"]
        report = await core.diagnose()
        assert "paint method: segment-none-indexed" in report and "scene (segment) effect" in report

        # custom scene animations obey the same rule: one band per colour
        e = fx.validate_segment_effect({"name": "x", "type": "breathe", "states": [[10, 20, 30, 0], [1, 2, 3, 0]]}, 50)
        await core.backend.segment_custom(s, e)
        assert h.calls[-1][1]["segments"] == [24, 49]
    run(go())
