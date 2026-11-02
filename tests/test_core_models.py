"""Core behaviour with the mock backend: other-model support, validation, live-mode races, storage."""

import asyncio
import os
import sys

import pytest

from tapo_studio.core import UserError, private_broadcast, private_ip


def run(coro):
    return asyncio.run(coro)


# ---- network address rules ------------------------------------------------

@pytest.mark.parametrize("ip", ["192.168.1.5", "10.1.2.3", "172.16.0.9", "172.31.255.1"])
def test_home_addresses_allowed(ip):
    assert private_ip(ip) == ip


@pytest.mark.parametrize("ip", [
    "8.8.8.8", "127.0.0.1", "0.0.0.0", "255.255.255.255", "169.254.1.1", "100.64.0.1", "198.18.0.1",
    "240.0.0.1", "192.0.0.8", "172.32.0.1", "192.168.1.255", "192.168.1.0",
    "::ffff:192.168.1.5", "3232235876", "0300.0250.0001.0002", "192.168.001.005", "10.0.0.1/8", "", "abc",
])
def test_other_addresses_rejected(ip):
    with pytest.raises(UserError):
        private_ip(ip)


def test_broadcast_addresses():
    assert private_broadcast("192.168.1.255") == "192.168.1.255"
    for bad in ("8.8.8.255", "255.255.255.255", "192.168.1.7"):
        with pytest.raises(UserError):
            private_broadcast(bad)


# ---- other models ---------------------------------------------------------

def test_capabilities_gate_actions(make_core):
    async def go():
        core = await make_core()
        strips = {s.name: s for s in core.strips.values()}
        lamp = next(s for s in strips.values() if s.model == "L530")
        strip = next(s for s in strips.values() if s.model == "L930")

        # effects: the strip plays it, the bulb is skipped with an explanation
        r = await core.action(None, "lighting_preset", {"name": "Aurora"})
        assert len(r["warnings"]) == 1 and "L530" in r["warnings"][0]
        assert strip.mode == "preset" and lamp.mode != "preset"

        # a request only a strip can do, aimed at a bulb, is an error
        with pytest.raises(UserError):
            await core.action([lamp.id], "paint", {"colors": [[0, 100, 100]]})

        # colour and brightness work on both
        r = await core.action(None, "color", {"h": 200, "s": 80, "b": 70})
        assert r["warnings"] == [] and lamp.mode == "color" and strip.mode == "color"
        await core.action(None, "brightness", {"value": 40})
        assert lamp.brightness == 40
    run(go())


def test_adding_and_changing_models(make_core):
    async def go():
        core = await make_core()
        with pytest.raises(UserError):                       # model is required when not discovered
            await core.add_device("192.168.1.77", "x", None, "P110")
        d = await core.add_device("192.168.1.77", "Hall", None, "L510B")
        assert d["model"] == "L510" and d["caps"] == ["brightness", "power"] and d["segments"] == 1
        with pytest.raises(UserError):
            await core.action([d["id"]], "color", {"h": 1, "s": 1, "b": 1})

        await core.update_device(d["id"], {"model": "L535"})
        assert "color" in core.strips[d["id"]].caps
        saved = next(x for x in core.store.data["devices"] if x["id"] == d["id"])
        assert saved["model"] == "L535"
        with pytest.raises(UserError):
            await core.update_device(d["id"], {"model": "nonsense"})
    run(go())


def test_old_settings_without_a_model_are_treated_as_strips(isolated_store):
    import json
    from tapo_studio.core import Core
    (isolated_store / "settings.json").write_text(json.dumps({"devices": [
        {"id": "a1", "ip": "192.168.1.9", "name": "Old strip", "segments": 50}]}))

    async def go():
        core = Core(mock=True)
        core.store.data["devices"] = json.loads((isolated_store / "settings.json").read_text())["devices"]
        await core.start()
        assert core.strips["a1"].model == "L930" and "zones" in core.strips["a1"].caps
    run(go())


def test_live_modes_drive_a_bulb_with_the_average_colour(make_core):
    async def go():
        core = await make_core()
        lamp = next(s for s in core.strips.values() if s.model == "L530")
        await core.live.start("music", {"source": "demo", "trigger": "bass", "style": "flash"}, [lamp.id])
        await asyncio.sleep(4)
        assert core.live.mode == "music" and core.live.music.an.locked()
        assert lamp.mode == "color" and len(lamp.frame) == lamp.segments == 1
        await core.live.stop()
        assert core.live.mode is None
    run(go())


# ---- live mode races (found by the security review) ------------------------

def test_overlapping_live_starts_leave_nothing_running(make_core):
    async def go():
        core = await make_core()
        ids = [s.id for s in core.strips.values()]
        await asyncio.gather(*(core.live.start("animation", {"animation": a}, ids)
                               for a in ["flow", "comet", "twinkle", "fire", "wave", "breathe"]))
        await core.live.stop()
        assert core.live.mode is None and core.live.task is None
        await asyncio.sleep(0.8)
        # no orphaned task keeps overwriting the strips
        await core.action(None, "color", {"h": 120, "s": 100, "b": 100})
        await asyncio.sleep(1.0)
        assert all(s.mode == "color" for s in core.strips.values())
    run(go())


# ---- schedules -------------------------------------------------------------

def test_scheduler_survives_garbage_in_the_settings_file(make_core):
    async def go():
        core = await make_core()
        core.store.data["schedules"] = ["junk", {"time": "00:00"}, {"id": 5, "enabled": True, "time": "99:99"}]
        # one pass of the loop body must not raise
        t = asyncio.create_task(core._scheduler())
        await asyncio.sleep(0.3)
        assert not t.done()
        t.cancel()
    run(go())


# ---- storage ---------------------------------------------------------------

def test_password_is_never_stored_in_clear(isolated_store):
    from tapo_studio.store import Store
    s = Store()
    s.set_credentials("me@example.com", "hunter2-secret")
    raw = (isolated_store / "settings.json").read_text()
    assert "hunter2-secret" not in raw
    assert Store().password == "hunter2-secret"
    assert "password_enc" not in s.public() and s.public()["has_password"] is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_settings_file_is_private(isolated_store):
    from tapo_studio.store import Store
    Store().set_credentials("a@b.c", "pw")
    assert oct(os.stat(isolated_store / "settings.json").st_mode & 0o777) == "0o600"


def test_corrupt_settings_do_not_crash_startup(isolated_store):
    from tapo_studio.store import Store
    (isolated_store / "settings.json").write_text("{ not json")
    assert Store().data["devices"] == []
    assert (isolated_store / "settings.broken.json").exists()
    (isolated_store / "settings.json").write_text('{"schedules": {}, "devices": [5, {"id": 1}], "prefs": []}')
    s = Store()
    assert s.data["schedules"] == [] and s.data["devices"] == [] and isinstance(s.data["prefs"], dict)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_dpapi_roundtrip_on_windows():
    from tapo_studio.store import protect, unprotect
    blob = protect("pässword 123")
    assert blob.startswith("dpapi:") and "pässword" not in blob
    assert unprotect(blob) == "pässword 123"
    assert unprotect("dpapi:" + "AAAA") == ""      # garbage in -> empty, not a crash


# ---- confirming that the light really shows what we sent --------------------

def first_strip(core):
    return next(s for s in core.strips.values() if "zones" in s.caps)


def test_a_light_that_ignores_the_usual_effect_id_is_retried_with_fresh_ids(make_core):
    from tapo_studio import effects as fx

    async def go():
        core = await make_core()
        s = first_strip(core)
        core.backend.ignored_ids = {fx.FRAME_ID}              # accepts the command, shows nothing
        r = await core.action([s.id], "paint", {"colors": [[0, 100, 100]]})
        assert r["warnings"] == [] and s.apply_mode == "fresh"          # the retry worked and was remembered
        assert next(d for d in core.store.data["devices"] if d["id"] == s.id)["apply_mode"] == "fresh"
        await core.action([s.id], "paint", {"colors": [[120, 100, 100]]})
        last = [x for x in core.backend.sent if x[0] == s.id and x[1] == "set_lighting_effect"][-1][2]
        assert last["id"] != fx.FRAME_ID                      # later paints use fresh ids straight away
    run(go())


def test_a_light_that_shows_nothing_is_reported_not_ignored(make_core):
    async def go():
        core = await make_core()
        s = first_strip(core)
        core.backend.ignore_all = True
        r = await core.action([s.id], "paint", {"colors": [[0, 100, 100]]})
        assert any("not showing it" in w for w in r["warnings"]), r
        r = await core.action([s.id], "effect", {"effect": {"type": "sequence", "sequence": [[0, 100, 100]]}})
        assert any("not showing it" in w for w in r["warnings"]), r
    run(go())


def test_diagnostics_report_has_no_secrets(make_core):
    async def go():
        core = await make_core()
        await core.set_credentials("secret.person@example.com", "hunter2-pw")
        report = await core.diagnose()
        assert "secret.person" not in report and "hunter2" not in report
        assert "192.168.1.50" not in report and "192.168.1.x" in report         # IPs are masked
        assert "L930" in report and "L530" in report and "test pattern" in report
        assert "AABBCC" not in report                                          # no hardware IDs
    run(go())


def test_diagnostics_skip_the_test_pattern_while_a_live_mode_runs(make_core):
    async def go():
        core = await make_core()
        await core.live.start("animation", {"animation": "flow"}, [s.id for s in core.strips.values()])
        report = await core.diagnose()
        assert "live mode is running" in report
        await core.live.stop()
    run(go())


def test_a_light_that_needs_its_running_effect_cleared_first(make_core):
    from tapo_studio import effects as fx

    async def go():
        core = await make_core()
        s = first_strip(core)
        # an old effect is playing; the light keeps showing it and ignores new patterns until a plain colour clears it
        s.info["lighting_effect"] = {"enable": 1, "id": "OLDEFFECT", "name": "Rainbow"}
        core.backend.needs_clear = True
        r = await core.action([s.id], "paint", {"colors": [[0, 100, 100]]})
        assert r["warnings"] == [] and s.apply_mode == "clear"
        # from now on it clears first, up front, so the first send already works
        n = len(core.backend.sent)
        r = await core.action([s.id], "paint", {"colors": [[120, 100, 100]]})
        steps = [w for _, w, _ in core.backend.sent[n:]]
        assert r["warnings"] == [] and steps[:2] == ["color", "set_lighting_effect"]
    run(go())


def test_an_old_effect_still_showing_is_not_mistaken_for_ours(make_core):
    async def go():
        core = await make_core()
        s = first_strip(core)
        s.info["lighting_effect"] = {"enable": 1, "id": "OLDEFFECT", "name": "Rainbow", "display_colors": [[0, 100, 100]]}
        info = {"lighting_effect": s.info["lighting_effect"]}
        assert core._showing(info, {"id": "NEWID", "name": "Tapo Studio", "display_colors": [[0, 100, 100]]}) is False
        same_id_new_colours = {"lighting_effect": {"enable": 1, "id": "X", "name": "n", "display_colors": [[10, 100, 100]]}}
        assert core._showing(same_id_new_colours, {"id": "X", "name": "n", "display_colors": [[200, 50, 50]]}) is False
        assert core._showing({"lighting_effect": {"enable": 1, "id": "X", "name": "n", "display_colors": [[10, 100, 100]]}},
                             {"id": "X", "name": "n", "display_colors": [[11, 99, 100]]}) is True       # rounding is fine
        assert core._showing({"lighting_effect": {"enable": 1}}, {"id": "X", "name": "n"}) is True        # can't tell: no alarm
        assert core._showing({"lighting_effect": {"enable": 0}}, {"id": "X", "name": "n"}) is False
    run(go())


# ---- firmware that ignores the usual way of painting -------------------------------

def test_painting_heals_itself_on_newer_firmware(make_core):
    from tapo_studio import effects as fx

    async def go():
        core = await make_core()
        s = first_strip(core)
        core.backend.custom_lighting_ignored = True                        # what a 1.4.x L930 does
        r = await core.action([s.id], "paint", {"colors": [[0, 100, 100], [240, 100, 100]]})
        assert r["warnings"] == [], r                                       # no user action needed
        assert s.paint_method == "segment-none-indexed"
        assert next(d for d in core.store.data["devices"] if d["id"] == s.id)["paint_method"] == "segment-none-indexed"
        sent = [(w, p) for _, w, p in core.backend.sent if w == "segment_custom"][-1][1]
        assert sent["type"] == "none" and sent["segments"] == list(range(50)) and len(sent["states"]) == 50
        # later paints go straight to the working method, with no failed attempts first
        n = len(core.backend.sent)
        await core.action([s.id], "paint", {"colors": [[120, 100, 100]]})
        assert "set_lighting_effect" not in [w for _, w, _ in core.backend.sent[n:]]
        # brightness changes and live frames use it too
        await core.action([s.id], "brightness", {"value": 40})
        assert core.backend.sent[-1][1] == "segment_custom"
        n = len(core.backend.sent)
        await core.live.start("animation", {"animation": "flow"}, [s.id])
        await asyncio.sleep(1.5)
        await core.live.stop()
        assert any(w == "segment_custom" for _, w, _ in core.backend.sent[n:])
    run(go())


def test_guided_test_reports_what_each_method_does(make_core):
    from tapo_studio import effects as fx

    async def go():
        core = await make_core()
        s = first_strip(core)
        core.backend.custom_lighting_ignored = True
        results = [await core.paint_test(s.id, i) for i in range(len(fx.FRAME_VARIANTS) + 1)]
        assert results[0]["control"] and results[0]["accepted"]            # built-in effects work
        accepted = [r["key"] for r in results[1:] if r["accepted"]]
        assert accepted == ["segment-none-indexed"], accepted
        single = next(r for r in results if r["key"] == "segment-none-count")
        assert single["accepted"] is False and "-1008" in single["error"]    # a refusal is reported, not a crash
        with pytest.raises(UserError):
            await core.paint_test(s.id, 99)
        await core.paint_choose(s.id, "segment-none-indexed")
        assert s.paint_method == "segment-none-indexed"
        assert [x for x in core.state()["strips"] if x["id"] == s.id][0]["paint_method"] == "segment-none-indexed"
        with pytest.raises(UserError):
            await core.paint_choose(s.id, "bogus")
        lamp = next(x for x in core.strips.values() if x.model == "L530")
        with pytest.raises(UserError):
            await core.paint_test(lamp.id, 0)                                  # bulbs have no zones to test
    run(go())


def test_when_nothing_works_the_user_is_told_what_to_press(make_core):
    async def go():
        core = await make_core()
        s = first_strip(core)
        core.backend.custom_lighting_ignored = True
        core.backend.segment_accept = lambda d: False
        r = await core.action([s.id], "paint", {"colors": [[0, 100, 100]]})
        assert "Fix painting" in r["warnings"][0]
    run(go())
