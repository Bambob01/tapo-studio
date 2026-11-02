"""App brain: devices, actions, scenes, schedules."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
import uuid

from . import color as C
from . import devices
from . import effects as fx
from .driver import Strip, StripError, local_broadcast, make_backend
from .effects import clean_name
from .live import ANIMATIONS, LiveEngine, Streamer
from .store import Store

log = logging.getLogger("tapo-studio")


class UserError(ValueError):
    """Message safe to show in the UI."""


HOME_NETS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def _ipv4(ip) -> ipaddress.IPv4Address:
    txt = str(ip).strip()
    try:
        return ipaddress.IPv4Address(txt)   # strict dotted-decimal; rejects octal/hex/short forms
    except ValueError:
        raise UserError("Enter an IP address like 192.168.1.50")


def private_ip(ip: str) -> str:
    """Only plain home-network IPv4 addresses (10.x, 172.16-31.x, 192.168.x)."""
    a = _ipv4(ip)
    net = next((n for n in HOME_NETS if a in n), None)
    if net is None or a in (net.network_address, net.broadcast_address) or str(a).endswith((".0", ".255")):
        raise UserError("Only devices on your home network (private IP addresses) are allowed")
    return str(a)


def private_broadcast(ip: str) -> str:
    a = _ipv4(ip)
    if not any(a in n for n in HOME_NETS) or not str(a).endswith(".255"):
        raise UserError("The search address should be your network's broadcast address, e.g. 192.168.1.255")
    return str(a)


# What a light must be able to do for each kind of action (see devices.py).
NEEDS = {"effect": "effects", "lighting_preset": "effects", "segment_preset": "effects",
         "segment_custom": "effects", "paint": "zones", "color": "color", "white": "white"}


class Core:
    def __init__(self, mock: bool = False):
        self.store = Store()
        self.backend = make_backend(mock)
        self.mock = self.backend.name == "mock"
        self.strips: dict[str, Strip] = {}
        self.streamers: dict[str, Streamer] = {}
        self.live = LiveEngine(self)
        self._sched_last: dict[str, str] = {}
        self.notice: str | None = None
        self.auth_failed = False

    # ---- startup --------------------------------------------------------
    async def start(self):
        self.backend.set_credentials(self.store.data["email"], self.store.password)
        if self.mock and not self.store.data["devices"]:
            self.store.data["devices"] = [
                {"id": "demo1", "ip": "192.168.1.50", "name": "Desk strip (demo)", "model": "L930", "segments": 50, "reverse": False},
                {"id": "demo2", "ip": "192.168.1.51", "name": "TV strip (demo)", "model": "L930", "segments": 50, "reverse": False},
                {"id": "demo3", "ip": "192.168.1.52", "name": "Lamp (demo)", "model": "L530", "segments": 1, "reverse": False},
            ]
        for d in self.store.data["devices"]:
            self._add_strip(d)
        asyncio.create_task(self.connect_all())
        asyncio.create_task(self._scheduler())
        asyncio.create_task(self._poll())

    def _add_strip(self, d):
        model = devices.base_model(d.get("model")) or "L930"     # devices saved before 0.1 had no model: strips
        s = Strip(d["id"], d["ip"], d["name"], int(d.get("segments", devices.family_for(model).zones)), model)
        s.reverse = bool(d.get("reverse"))
        s.mac = d.get("mac") or None
        s.info_mac = d.get("info_mac") or None
        s.apply_mode = d.get("apply_mode") or ("fresh" if d.get("unique_ids") else "fixed")
        s.paint_method = d.get("paint_method") if d.get("paint_method") in dict((k, 1) for k, _, _ in fx.FRAME_VARIANTS) \
            else fx.DEFAULT_PAINT_METHOD
        s.frame = [[0, 0, 0]] * s.segments
        self.strips[s.id] = s
        return s

    def streamer(self, s: Strip) -> Streamer:
        if s.id not in self.streamers:
            self.streamers[s.id] = Streamer(self, s)
        return self.streamers[s.id]

    async def connect_all(self):
        if not self.mock and not (self.store.data["email"] and self.store.password):
            return
        self.auth_failed = False
        # One at a time: repeated failed logins can lock the strip out for a while.
        for s in list(self.strips.values()):
            await self.connect(s)
            if self.auth_failed:
                break

    async def connect(self, s: Strip):
        try:
            await self.backend.connect(s)
            s.error = None
            # Remember the strip's hardware address so a different device that
            # later takes this IP never receives a login attempt.
            d = next((x for x in self.store.data["devices"] if x["id"] == s.id), None)
            if d is not None:
                new = {"mac": s.mac or d.get("mac"), "info_mac": s.info_mac or d.get("info_mac"), "model": s.model}
                if any(d.get(k) != v for k, v in new.items()):
                    d.update(new)
                    if "zones" not in s.caps:
                        d["segments"] = s.segments = 1
                    self.store.save()
        except Exception as e:
            s.connected = False
            msg = str(e)
            if any(k in msg.lower() for k in ("forbidden", "unauthor", "hash_mismatch", "login", "credential")):
                self.auth_failed = True
                msg = ("Login refused. Check your Tapo email/password (case-sensitive). If it still fails, "
                       "toggle Tapo app > Me > Third-Party Services > Third-Party Compatibility, wait a minute, "
                       "and press Reconnect. (" + msg[:160] + ")")
            s.error = msg

    async def _poll(self):
        while True:
            await asyncio.sleep(20)
            if self.live.mode in ("animation", "screen", "music"):
                continue
            for s in list(self.strips.values()):
                if s.connected and not s.lock.locked():
                    try:
                        await self.backend.refresh(s)
                    except Exception:
                        pass

    # ---- settings & devices --------------------------------------------
    async def set_credentials(self, email: str, password: str | None):
        email = clean_name(email, "", 120)
        if email and "@" not in email:
            raise UserError("That doesn't look like an email address")
        self.store.set_credentials(email, password)
        self.backend.set_credentials(self.store.data["email"], self.store.password)
        for s in self.strips.values():
            s.connected = False
        await self.connect_all()

    async def discover(self, target: str | None):
        target = private_broadcast(target) if target else local_broadcast()
        try:
            found = await self.backend.discover(target, 4)
        except StripError as e:
            raise UserError(str(e))
        except Exception as e:
            log.warning("Discovery failed: %s", e)
            raise UserError("Search failed - check the PC is on the same network as the strips")
        known = {s.ip for s in self.strips.values()}
        for f in found:
            f["added"] = f.get("ip") in known
            if f.get("ip"):
                try:
                    private_ip(f["ip"])
                except UserError:
                    f["supported"] = False
        self._last_discovery = {f["ip"]: f for f in found if f.get("ip")}
        return {"target": target, "found": found}

    async def add_device(self, ip, name, segments=None, model=None):
        ip = private_ip(ip)
        if any(s.ip == ip for s in self.strips.values()):
            raise UserError("That light is already added")
        if len(self.strips) >= 16:
            raise UserError("Maximum of 16 lights")
        seen = getattr(self, "_last_discovery", {}).get(ip, {})
        base = devices.base_model(model) or devices.base_model(seen.get("model"))
        if not base:
            raise UserError("Choose the model of your light (see the list in Settings)")
        fam = devices.family_for(base)
        zones = max(1, min(100, int(segments))) if segments and "zones" in fam.caps else fam.zones
        d = {"id": uuid.uuid4().hex[:10], "ip": ip, "name": clean_name(name, f"Tapo {base}"), "model": base,
             "segments": zones, "reverse": False, "mac": seen.get("mac")}
        self.store.data["devices"].append(d)
        self.store.save()
        s = self._add_strip(d)
        await self.connect(s)
        return s.public()

    async def update_device(self, dev_id, patch):
        d = next((x for x in self.store.data["devices"] if x["id"] == dev_id), None)
        if not d:
            raise UserError("Unknown strip")
        s = self.strips[dev_id]
        if "name" in patch:
            d["name"] = s.name = clean_name(patch["name"], d["name"])
        if "model" in patch:
            base = devices.base_model(patch["model"])
            if not base:
                raise UserError("Unsupported model")
            if base != s.model:
                d["model"], s.model = base, base
                d["segments"] = s.segments = devices.family_for(base).zones
                s.frame = [[0, 0, 0]] * s.segments
                self.backend.forget(s)
                s.connected = False
                self.store.save()
                await self.connect(s)
        if "segments" in patch and "zones" in s.caps:
            d["segments"] = s.segments = max(1, min(100, int(patch["segments"])))
            s.frame = (s.frame + [[0, 0, 0]] * 100)[:s.segments]
        if "reverse" in patch:
            d["reverse"] = s.reverse = bool(patch["reverse"])
        self.store.save()

    async def remove_device(self, dev_id):
        self.store.data["devices"] = [x for x in self.store.data["devices"] if x["id"] != dev_id]
        self.store.save()
        self.strips.pop(dev_id, None)
        st = self.streamers.pop(dev_id, None)
        if st:
            st.stop()

    def targets(self, ids) -> list[Strip]:
        ids = ids or list(self.strips)
        out = [self.strips[i] for i in ids if i in self.strips]
        if not out:
            raise UserError("No lights selected - tick at least one light at the top of the page")
        return out

    # ---- actions --------------------------------------------------------
    async def _each(self, strips, fn):
        res = await asyncio.gather(*(fn(s) for s in strips), return_exceptions=True)
        errs, notes = [], []
        for s, r in zip(strips, res):
            if isinstance(r, str):                     # an action can return a warning about its light
                notes.append(r)
            elif isinstance(r, (UserError, StripError, fx.EffectError)):
                errs.append(f"{s.name}: {r}")
            elif isinstance(r, Exception):
                log.error("Action failed on %s", s.name, exc_info=r)
                errs.append(f"{s.name}: failed (details in the app window)")
        if errs and len(errs) == len(strips):
            raise UserError("; ".join(errs)[:400])
        return errs + notes

    # ---- confirming that a light really shows what we sent ---------------
    @staticmethod
    def _showing(info: dict, effect: dict) -> bool:
        """Does the light report that *this* effect is the one it is showing?

        A bare "an effect is enabled" is not enough: if the light ignored our command it keeps
        showing its previous effect, and that is enabled too."""
        le = info.get("lighting_effect") or {}
        if not le.get("enable"):
            return False
        rid, rname = le.get("id") or "", le.get("name") or ""
        if rid:
            if rid != effect.get("id"):
                return False                                   # a different effect is showing
        elif rname and rname != effect.get("name"):
            return False
        want, got = (effect.get("display_colors") or [None])[0], (le.get("display_colors") or [None])[0]
        if want and got and len(want) == len(got) >= 3:         # same content, not just the same id
            return all(abs(int(a) - int(b)) <= 2 for a, b in zip(want[:3], got[:3]))
        return True

    @staticmethod
    def _scene_showing(info: dict, effect: dict) -> bool:
        se = info.get("segment_effect") or {}
        if not se.get("enable"):
            return False
        rid = se.get("id") or ""
        return not rid or rid == effect.get("id")

    def _frame_showing(self, info: dict, eff: dict, method: str) -> bool:
        return self._scene_showing(info, eff) if fx.variant_kind(method) == "segment" else self._showing(info, eff)

    async def _send_frame(self, s: Strip, colors: list, trans: int, bri: int, *, method: str | None = None,
                          effect_id: str | None = None) -> dict:
        """Paint the zones using this light's paint method. Returns the request body that was sent."""
        method = method or s.paint_method
        eff = fx.build_frame(method, colors, trans, bri, effect_id)
        if fx.variant_kind(method) == "segment":
            await self.backend.segment_custom(s, eff)
        else:
            await self.backend.lighting(s, eff)
        return eff

    def _fresh_id(self, s: Strip) -> str | None:
        """A new effect id when this light needs one (lighting methods only), else None."""
        if fx.variant_kind(s.paint_method) == "segment" or s.apply_mode == "fixed":
            return None
        return fx.prefixed_id() if s.paint_method == "lighting-prefixed-id" else fx.new_id()

    async def _clear_effects(self, s: Strip, brightness: int = 60):
        """A plain colour command removes any running effect or scene (documented by the tapo library)."""
        await self.backend.color(s, 0, 1, max(1, min(100, int(brightness))))

    def _set_paint_method(self, s: Strip, key: str, apply_mode: str | None = None):
        s.paint_method = key
        if apply_mode:
            s.apply_mode = apply_mode
        d = next((x for x in self.store.data["devices"] if x["id"] == s.id), None)
        if d is not None:
            d["paint_method"] = key
            if apply_mode:
                d["apply_mode"] = apply_mode
            self.store.save()

    def _remember_mode(self, s: Strip, mode: str):
        order = ("fixed", "fresh", "clear")
        if order.index(mode) > order.index(s.apply_mode):
            s.apply_mode = mode
            d = next((x for x in self.store.data["devices"] if x["id"] == s.id), None)
            if d is not None:
                d["apply_mode"] = mode
                self.store.save()

    async def _confirm_effect(self, s: Strip, effect: dict, method: str | None = None,
                              heal: tuple | None = None) -> str | None:
        """Read back whether the light is really showing `effect`. Returns a warning, or None when confirmed.

        Lights can accept a command and show nothing new. For lighting effects we escalate, checking after
        each step: (1) a fresh effect id, then (2) clearing whatever is running first. What works is remembered."""
        method = method or "lighting-default"
        try:
            info = await self.backend.refresh(s)
        except Exception:
            return None                    # can't check: don't raise a false alarm
        if self._frame_showing(info, effect, method):
            return None
        if fx.variant_kind(method) == "lighting":
            for mode in ("fresh", "clear"):
                retry = {**effect, "id": fx.prefixed_id() if method == "lighting-prefixed-id" else fx.new_id()}
                try:
                    if mode == "clear":
                        await self._clear_effects(s, effect.get("brightness", 100))
                    await self.backend.lighting(s, retry)
                    info = await self.backend.refresh(s)
                except Exception as e:
                    return f"{s.name}: the pattern could not be confirmed ({str(e)[:120]})"
                if self._frame_showing(info, retry, method):
                    self._remember_mode(s, mode)
                    return None
        if heal and method == fx.DEFAULT_PAINT_METHOD:
            # Newer firmware accepts the lighting-effect request but never applies it, and paints through the
            # scene request instead. Try that with the same colours; if the light confirms it, keep using it.
            colors, trans, bri = heal
            try:
                alt = await self._send_frame(s, colors, trans, bri, method="segment-none-indexed")
                info = await self.backend.refresh(s)
                if self._frame_showing(info, alt, "segment-none-indexed"):
                    self._set_paint_method(s, "segment-none-indexed", "fixed")
                    return None
            except Exception as e:
                log.info("Scene-style painting not accepted by %s: %s", s.name, e)
        le = info.get("lighting_effect") or {}
        extras = []
        if (info.get("segment_effect") or {}).get("enable") and fx.variant_kind(method) == "lighting":
            extras.append("A scene animation is also active.")
        if info.get("music_rhythm_enable"):
            extras.append("Music sync is switched on in the Tapo app.")
        return (f"{s.name} accepted the pattern but is not showing it (it reports the effect "
                f"{le.get('name') or 'none'!r}, enabled={le.get('enable')}). {' '.join(extras)} "
                "Open Settings > Troubleshooting and press \"Fix painting\".").replace("  ", " ")

    # ---- finding out how a particular light must be painted ---------------------
    def _zoned_strip(self, strip_id: str) -> Strip:
        s = self.strips.get(str(strip_id))
        if not s or "zones" not in s.caps:
            raise UserError("Choose a light strip with zones")
        if not s.connected:
            raise UserError("That light is not connected")
        return s

    def _test_pattern(self, s: Strip) -> list:
        half = s.segments // 2
        return [[0, 100, 100]] * half + [[240, 100, 100]] * (s.segments - half)

    @staticmethod
    def _brief(info: dict) -> dict:
        le, se = info.get("lighting_effect") or {}, info.get("segment_effect") or {}
        return {"lighting": f"{le.get('name')!r} enabled={le.get('enable')}",
                "scene": f"{se.get('name')!r} enabled={se.get('enable')}"}

    async def paint_test(self, strip_id: str, index: int) -> dict:
        """Step `index` of the guided test. Step 0 is a control (a built-in effect); the rest try one paint
        method each from a clean state. `accepted` means the light itself reports it is showing that request."""
        s = self._zoned_strip(strip_id)
        total = len(fx.FRAME_VARIANTS) + 1
        if not 0 <= index < total:
            raise UserError("No such test")
        await self.live.stop([s.id])
        if index == 0:
            label, key, control = "Built-in effect (Rainbow)", "control", True
        else:
            key, _, label = fx.FRAME_VARIANTS[index - 1]
            control = False
        base = {"index": index, "total": total, "key": key, "label": label, "control": control}
        try:
            if control:
                await self.backend.lighting_preset(s, "Rainbow")
                info = await self.backend.refresh(s)
                le = info.get("lighting_effect") or {}
                ok = bool(le.get("enable") and le.get("name") == "Rainbow")
            else:
                await self._clear_effects(s, 100)
                eff = await self._send_frame(s, self._test_pattern(s), 0, 100, method=key)
                info = await self.backend.refresh(s)
                ok = self._frame_showing(info, eff, key)
        except Exception as e:      # the light itself rejected the request: that is an answer, not a crash
            return {**base, "accepted": False, "error": str(e)[:200], "readback": {"lighting": "-", "scene": "-"}}
        return {**base, "accepted": ok, "readback": self._brief(info)}

    async def paint_choose(self, strip_id: str, key: str) -> dict:
        s = self._zoned_strip(strip_id)
        if key not in {k for k, _, _ in fx.FRAME_VARIANTS}:
            raise UserError("Unknown paint method")
        self._set_paint_method(s, key, "fixed")
        await self._send_frame(s, self._test_pattern(s), 0, 100)
        return {"ok": True, "method": key}

    async def diagnose(self, test_pattern: bool = True) -> str:
        """A plain-text report for bug reports. Contains no login details, hardware IDs or full IP addresses."""
        import platform
        import sys
        from . import __version__

        def mask(ip):
            return ".".join(str(ip).split(".")[:3] + ["x"])

        lines = [f"Tapo Studio {__version__} | Python {sys.version.split()[0]} | {platform.platform()}",
                 f"Backend: {self.backend.name} | lights: {len(self.strips)}", ""]
        for s in self.strips.values():
            fam = devices.family_for(s.model)
            lines.append(f"{s.name}: {s.model} ({fam.label if fam else 'unknown'}) at {mask(s.ip)}")
            lines.append(f"  connected: {s.connected} | last error: {s.error or 'none'} | mode: {s.mode} | "
                         f"apply mode: {s.apply_mode} | paint method: {s.paint_method}")
            if not s.connected:
                continue
            try:
                info = await self.backend.refresh(s)
                le, se = info.get("lighting_effect") or {}, info.get("segment_effect") or {}
                lines.append(f"  firmware: {info.get('fw_ver')} | on: {info.get('device_on')} | "
                             f"brightness: {info.get('brightness')} | wifi signal: {info.get('rssi')}")
                lines.append(f"  device reports lighting effect: name={le.get('name')!r} id={str(le.get('id'))[:8]!r} "
                             f"enabled={le.get('enable')} | scene animation enabled={se.get('enable')} | "
                             f"phone music sync: {info.get('music_rhythm_enable')}")
                lines.append(f"  device reports scene (segment) effect: name={se.get('name')!r} id={str(se.get('id'))[:8]!r} "
                             f"enabled={se.get('enable')}")
                if test_pattern and "zones" in s.caps and self.live.mode in ("animation", "screen", "music"):
                    lines.append("  (a live mode is running, so the test pattern was skipped - press Stop first)")
                elif test_pattern and "zones" in s.caps:
                    if s.apply_mode == "clear" and fx.variant_kind(s.paint_method) == "lighting":
                        await self._clear_effects(s, 100)
                    eff = await self._send_frame(s, self._test_pattern(s), 0, 100, effect_id=self._fresh_id(s))
                    s.mode = "frame"
                    pattern = self._test_pattern(s)
                    warn = await self._confirm_effect(s, eff, s.paint_method, heal=(pattern, 0, 100))
                    lines.append("  test pattern (first half RED, second half BLUE): "
                                 + ("the light confirms it is showing it - did you see red then blue along the strip?"
                                    if not warn else warn.split(" Open Settings")[0]))
            except Exception as e:
                lines.append(f"  could not read the light: {str(e)[:160]}")
        return "\n".join(lines)

    async def action(self, ids, kind: str, p: dict, from_engine=False):
        strips = self.targets(ids)
        if not from_engine and kind not in ("brightness", "refresh"):
            await self.live.stop([s.id for s in strips])
        b = self.backend
        offline = [s for s in strips if not s.connected]
        strips = [s for s in strips if s.connected]
        if not strips:
            raise UserError("Not connected" + (f": {offline[0].error}" if offline and offline[0].error else
                                                " - check Settings"))
        # Skip lights that can't do what was asked (e.g. effects on a plain bulb).
        skipped = []
        need = NEEDS.get(kind)
        if need:
            capable = [s for s in strips if need in s.caps]
            skipped = [f"{s.name}: an {s.model} has no {devices.CAP_LABELS[need]}" for s in strips if need not in s.caps]
            if not capable:
                raise UserError("; ".join(skipped))
            strips = capable

        if kind == "power":
            on = bool(p.get("on"))

            async def f(s):
                await b.power(s, on)
        elif kind == "brightness":
            v = max(1, min(100, int(p.get("value", 100))))

            async def f(s):
                if s.mode == "effect" and s.last_effect:
                    e = dict(s.last_effect, brightness=v)
                    await b.lighting(s, e)
                    s.last_effect = e
                elif s.mode == "frame":
                    await self._send_frame(s, s.frame, 300, v)
                else:
                    await b.brightness(s, v)
                s.brightness = v
        elif kind == "color":
            h, sat, v = int(p.get("h", 0)) % 361, max(1, min(100, int(p.get("s", 100)))), max(1, min(100, int(p.get("b", 100))))

            async def f(s):
                await b.color(s, h, sat, v)
                s.mode, s.brightness, s.on = "color", v, True
                s.frame = [[h, sat, v]] * s.segments
        elif kind == "white":
            k, v = max(2500, min(6500, int(p.get("k", 4000)))), max(1, min(100, int(p.get("b", 100))))

            async def f(s):
                await b.white(s, k, v)
                s.mode, s.brightness, s.on = "white", v, True
                s.frame = [[30, max(0, int((6500 - k) / 4000 * 60)), v]] * s.segments
        elif kind == "effect":
            try:
                e = fx.validate_lighting(p.get("effect") or {})
            except fx.EffectError as ex:
                raise UserError(str(ex))

            async def f(s):
                eff = {**e, "id": fx.new_id()} if s.apply_mode != "fixed" else e
                if s.apply_mode == "clear":
                    await self._clear_effects(s, e.get("brightness", 100))
                await b.lighting(s, eff)
                s.mode, s.last_effect, s.on = "effect", eff, True
                return await self._confirm_effect(s, eff)
        elif kind == "lighting_preset":
            preset = fx.preset_lighting(str(p.get("name", "")))
            if not preset:
                raise UserError("Unknown preset")

            async def f(s):
                await b.lighting_preset(s, preset["name"])
                s.mode, s.last_effect, s.on = "preset", None, True
        elif kind == "segment_preset":
            sp = fx.preset_segment(str(p.get("enum", "")))
            if not sp:
                raise UserError("Unknown scene")

            async def f(s):
                await b.segment_preset(s, sp["enum"])
                s.mode, s.on = "segment", True
        elif kind == "segment_custom":
            async def f(s):
                try:
                    e = fx.validate_segment_effect(p.get("effect") or {}, s.segments)
                except fx.EffectError as ex:
                    raise UserError(str(ex))
                await b.segment_custom(s, e)
                s.mode, s.on = "segment", True
        elif kind == "paint":
            cols = [fx.hsb(c) for c in (p.get("colors") or [])][:100]
            if not cols:
                raise UserError("Nothing to paint")
            trans = max(0, min(5000, int(p.get("transition", 300))))
            v = max(1, min(100, int(p.get("brightness", 100))))

            async def f(s):
                frame = (cols * (s.segments // len(cols) + 1))[:s.segments] if p.get("repeat") else \
                    (cols + [cols[-1]] * s.segments)[:s.segments]
                if s.reverse:
                    frame = frame[::-1]
                if s.apply_mode == "clear" and fx.variant_kind(s.paint_method) == "lighting":
                    await self._clear_effects(s, v)
                eff = await self._send_frame(s, frame, trans, v, effect_id=self._fresh_id(s))
                s.frame = frame[::-1] if s.reverse else frame
                s.mode, s.on, s.brightness = "frame", True, v
                st = self.streamers.get(s.id)
                if st:
                    st.reset()
                return await self._confirm_effect(s, eff, s.paint_method, heal=(frame, trans, v))
        elif kind == "refresh":
            async def f(s):
                await b.refresh(s)
        elif kind == "reboot":
            async def f(s):
                await b.reboot(s)
                s.connected = False
        else:
            raise UserError("Unknown action")
        errs = await self._each(strips, f)
        return {"ok": True, "warnings": errs + skipped + [f"{s.name}: offline" for s in offline]}

    # ---- scenes ---------------------------------------------------------
    SCENE_KINDS = {"paint", "effect", "lighting_preset", "segment_preset", "segment_custom", "color", "white", "power"}

    def find_scene(self, sid):
        return next((x for x in self.store.data["scenes"] if x["id"] == sid), None)

    async def apply_scene(self, scene, ids, from_engine=False):
        kind = scene["kind"]
        if kind not in self.SCENE_KINDS:
            raise UserError("Unknown scene type")
        return await self.action(ids, kind, scene.get("data") or {}, from_engine=from_engine)

    def save_scene(self, name, kind, data, sid=None):
        if kind not in self.SCENE_KINDS:
            raise UserError("Unknown scene type")
        scenes = self.store.data["scenes"]
        if len(scenes) >= 300 and not sid:
            raise UserError("Scene limit reached")
        sc = {"id": sid or uuid.uuid4().hex[:10], "name": clean_name(name, "My scene"), "kind": kind, "data": data}
        self.store.data["scenes"] = [x for x in scenes if x["id"] != sc["id"]] + [sc]
        self.store.save()
        return sc

    # ---- schedules ------------------------------------------------------
    async def run_schedule_action(self, sch):
        a = sch.get("action") or {}
        ids = sch.get("targets") or None
        if a.get("type") == "scene":
            sc = self.find_scene(a.get("scene_id"))
            if sc:
                await self.apply_scene(sc, ids)
        elif a.get("type") in ("on", "off"):
            await self.action(ids, "power", {"on": a["type"] == "on"})
        elif a.get("type") == "playlist":
            await self.live.start("playlist", {"playlist_id": a.get("playlist_id")}, [s.id for s in self.targets(ids)])
        elif a.get("type") == "daylight":
            await self.live.start("daylight", a.get("params") or {}, [s.id for s in self.targets(ids)])
        elif a.get("type") == "preset":
            await self.action(ids, "lighting_preset", {"name": a.get("name")})

    async def _scheduler(self):
        while True:
            now = time.localtime()
            hm = f"{now.tm_hour:02d}:{now.tm_min:02d}"
            stamp = time.strftime("%Y-%m-%d %H:%M", now)
            for sch in list(self.store.data.get("schedules") or []):
                try:
                    if not isinstance(sch, dict) or not sch.get("enabled", True) or sch.get("time") != hm:
                        continue
                    days = sch.get("days")
                    if days is None:
                        days = range(7)
                    if now.tm_wday not in days:
                        continue
                    sid = str(sch.get("id"))
                    if self._sched_last.get(sid) == stamp:
                        continue
                    self._sched_last[sid] = stamp
                    await self.run_schedule_action(sch)
                    self.notice = f"Schedule ran at {hm}"
                except Exception as e:
                    log.warning("Schedule failed: %s", e)
                    self.notice = f"Schedule at {hm} failed: {e}"
            await asyncio.sleep(15)

    # ---- state ----------------------------------------------------------
    def state(self):
        return {
            "mock": self.mock,
            "strips": [s.public() for s in self.strips.values()],
            "live": self.live.public(),
            "notice": self.notice,
            "auth_failed": self.auth_failed,
        }

    def catalog(self):
        return {
            "lighting_presets": fx.LIGHTING_PRESETS,
            "studio_effects": fx.STUDIO_EFFECTS,
            "segment_presets": fx.SEGMENT_PRESETS,
            "segment_types": fx.SEGMENT_TYPES,
            "lighting_types": fx.LIGHTING_TYPES,
            "models": devices.catalog(),
            "palettes": C.PALETTES,
            "animations": list(ANIMATIONS),
        }
