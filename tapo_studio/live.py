"""Live modes: PC-driven frames (animations, screen sync, music) plus slow
automations (daylight white, playlists).

The strip is the bottleneck (~0.3 s per write), so each strip has a
`Streamer`. It keeps exactly one request in flight, always sends the newest
frame, skips frames that wouldn't look different, and lets the strip fade
between frames itself using the effect's transition time.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import random
import time

import numpy as np

from . import color as C
from . import effects as fx
from .audio import AudioCapture, BeatAnalyzer


class Streamer:
    def __init__(self, core, strip):
        self.core, self.strip = core, strip
        self.latest = None
        self.event = asyncio.Event()
        self.last_sent: np.ndarray | None = None
        self.task = asyncio.create_task(self._run())
        self.sent = 0

    def submit(self, rgb_frame, transition_ms=None):
        self.latest = (rgb_frame, transition_ms)
        self.event.set()

    def reset(self):
        self.last_sent = None
        self.latest = None

    def stop(self):
        self.task.cancel()

    async def _run(self):
        while True:
            await self.event.wait()
            self.event.clear()
            if self.latest is None:
                continue
            rgb, trans = self.latest
            self.latest = None
            s = self.strip
            prefs = self.core.store.data["prefs"]
            zoned = "zones" in s.caps
            if zoned:
                if s.reverse:
                    rgb = rgb[::-1]
                hsb = np.array([C.rgb_to_hsb(c) for c in rgb])
            else:
                # One-zone lights (bulbs): show the average colour of the frame.
                hsb = np.array([C.rgb_to_hsb(np.mean(np.array(rgb, float), axis=0))])
            if self.last_sent is not None and self.last_sent.shape == hsb.shape:
                d = np.abs(hsb - self.last_sent).astype(float)
                d[:, 0] = np.minimum(d[:, 0], 360 - d[:, 0]) / 3.6
                if d.max() < 2:
                    continue
            if trans is None:
                r = sorted(s.rtt)
                rtt = r[len(r) * 3 // 4] if r else 0.3
                trans = int(min(900, max(60, rtt * 1000))) if prefs.get("auto_transition", True) else 0
            t0 = time.perf_counter()
            try:
                if zoned:
                    await self.core._send_frame(s, hsb.tolist(), trans, 100, effect_id=self.core._fresh_id(s))
                    s.mode = "frame"
                elif "color" in s.caps:
                    h_, sat, v = (int(x) for x in hsb[0])
                    await self.core.backend.color(s, h_, max(1, sat), max(1, v))
                    s.mode = "color"
                else:                       # brightness-only bulb: follow the loudness/brightness
                    await self.core.backend.brightness(s, max(1, int(hsb[0][2])))
                self.last_sent = hsb
                s.frame = hsb.tolist() if zoned else [hsb[0].tolist()] * s.segments
                self.sent += 1
            except Exception:
                await asyncio.sleep(1.0)
            min_gap = 1.0 / max(0.5, float(prefs.get("max_fps", 4)))
            await asyncio.sleep(max(0.0, min_gap - (time.perf_counter() - t0)))


# ---------------------------------------------------------------------------
# Frame sources. render(t, n, p) -> list of n RGB colours.
# ---------------------------------------------------------------------------

def _pal(p) -> list[list[int]]:
    cols = p.get("palette_colors")
    if not cols:
        name = p.get("palette", "Sunset")
        cols = C.PALETTES.get(name) or C.PALETTES["Sunset"]
    return [C.hex_to_rgb(h) if isinstance(h, str) else list(h) for h in cols]


def _scale(c, v):
    return [int(x * v) for x in c]


def anim_flow(t, n, p):
    pal = _pal(p)
    sp, sc = p.get("speed", 0.5), p.get("scale", 1.0)
    return [C.sample(pal, i / n * sc + t * sp * 0.15) for i in range(n)]


def anim_breathe(t, n, p):
    pal = _pal(p)
    sp = p.get("speed", 0.5)
    phase = t * sp * 0.6
    v = 0.15 + 0.85 * (0.5 - 0.5 * math.cos(phase * 2 * math.pi))
    c = C.sample(pal, phase / 4)
    return [_scale(c, v)] * n


def anim_comet(t, n, p):
    pal = _pal(p)
    sp = p.get("speed", 0.5)
    tail = max(2, int(p.get("scale", 1.0) * n / 4))
    head = (t * sp * n * 0.25) % (n + tail)
    out = []
    for i in range(n):
        d = head - i
        v = math.exp(-d / (tail / 3)) if 0 <= d <= tail else 0.0
        out.append(_scale(C.sample(pal, i / n), max(0.03, v)))
    return out


def anim_twinkle(t, n, p):
    pal = _pal(p)
    sp = p.get("speed", 0.5)
    out = []
    for i in range(n):
        rnd = random.Random(i * 7919)
        period = 1.5 + rnd.random() * 3 / max(0.1, sp)
        ph = (t / period + rnd.random()) % 1
        v = max(0.05, math.sin(ph * math.pi) ** 6)
        out.append(_scale(pal[rnd.randrange(len(pal))], v))
    return out


def anim_fire(t, n, p):
    sp = p.get("speed", 0.5)
    pal = [C.hex_to_rgb(h) for h in ["#200000", "#ff2000", "#ff8000", "#ffd060"]]
    out = []
    for i in range(n):
        heat = 0.5 + 0.25 * math.sin(i * 0.7 + t * 3 * sp) + 0.25 * math.sin(i * 0.23 - t * 5.3 * sp)
        heat *= 0.7 + 0.3 * random.random()
        out.append(C.sample(pal, min(0.74, max(0, heat)) ))
    return out


def anim_wave(t, n, p):
    pal = _pal(p)
    sp, sc = p.get("speed", 0.5), p.get("scale", 1.0)
    a, b = pal[0], pal[1 % len(pal)]
    out = []
    for i in range(n):
        f = 0.5 + 0.5 * math.sin((i / n * 4 * sc - t * sp) * math.pi)
        out.append([int(a[k] * f + b[k] * (1 - f)) for k in range(3)])
    return out


ANIMATIONS = {"flow": anim_flow, "breathe": anim_breathe, "comet": anim_comet,
              "twinkle": anim_twinkle, "fire": anim_fire, "wave": anim_wave}


class ScreenSource:
    """Averages screen regions into zone colours (ambilight)."""

    def __init__(self):
        self.sct = None
        self.smooth: np.ndarray | None = None
        self.error = None

    def render(self, t, n, p):
        if self.sct is None:
            try:
                import mss
                self.sct = mss.mss()
            except Exception as e:
                self.error = f"Screen sync needs the 'mss' package. Run install-extras.bat. ({e})"
                raise RuntimeError(self.error)
        mons = self.sct.monitors
        mi = int(p.get("monitor", 1))
        mon = mons[mi if 0 < mi < len(mons) else 1]
        shot = self.sct.grab(mon)
        img = np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)[::8, ::8, 2::-1].astype(float)
        h, w, _ = img.shape
        depth = max(1, int(min(h, w) * float(p.get("depth", 0.15))))
        region = p.get("region", "full")
        if region == "average":
            cols = np.repeat(img.reshape(-1, 3).mean(0)[None], n, 0)
        elif region == "around":
            # clockwise from bottom-left: left (up), top, right (down), bottom (back)
            perim = np.concatenate([img[::-1, :depth].mean(1), img[:depth].mean(0),
                                    img[:, -depth:].mean(1), img[-depth:, ::-1].mean(0)])
            cols = np.array([perim[int(i * len(perim) / n)] for i in range(n)])
        else:
            band = {"top": img[:depth].mean(0), "bottom": img[-depth:].mean(0),
                    "left": img[::-1, :depth].mean(1), "right": img[::-1, -depth:].mean(1),
                    "full": img.mean(0)}.get(region, img.mean(0))
            idx = np.linspace(0, len(band), n + 1).astype(int)
            cols = np.array([band[idx[i]:max(idx[i] + 1, idx[i + 1])].mean(0) for i in range(n)])
        # saturation boost
        boost = float(p.get("saturation", 1.4))
        grey = cols.mean(1, keepdims=True)
        cols = np.clip(grey + (cols - grey) * boost, 0, 255)
        cols *= float(p.get("intensity", 1.0))
        a = float(p.get("smoothing", 0.5))
        if self.smooth is None or self.smooth.shape != cols.shape:
            self.smooth = cols
        else:
            self.smooth = self.smooth * a + cols * (1 - a)
        return np.clip(self.smooth, 0, 255).astype(int).tolist()


class MusicSource:
    def __init__(self):
        self.an = BeatAnalyzer()
        self.cap = AudioCapture(self.an)
        self.beat_index = 0
        self._last_trigger = -1.0
        self._prev_env = 0.0

    def start(self, p):
        self.an.sensitivity = float(p.get("sensitivity", 0.5))
        self.cap.start(p.get("source", "loopback"))

    def stop(self):
        self.cap.stop()

    def triggers(self, p, t_target) -> list[float]:
        band = p.get("trigger", "bass")
        use_pred = p.get("predict", True) and band in ("bass", "any") and self.an.locked()
        hits = [h for h, _ in self.an.recent_hits(band, t_target - 3)]
        if not use_pred:
            return hits
        # Real hits always count, so fast playing between beats isn't lost. Predicted
        # beats only fill in while real hits are still arriving near them.
        period = self.an.period
        beats = [b for b in self.an.beat_times(t_target - 3, t_target)
                 if any(b - 2 * period <= h <= b + 0.1 for h in hits) or (hits and hits[-1] > t_target - 2 * period)]
        return sorted(beats + [h for h in hits if all(abs(h - b) > 0.06 for b in beats)])

    def render(self, t_target, n, p):
        self.an.sensitivity = float(p.get("sensitivity", 0.5))
        pal = _pal(p)
        style = p.get("style", "flash")
        decay = max(0.05, float(p.get("decay_ms", 350)) / 1000)
        floor = float(p.get("floor", 0.08))
        trig = [x for x in self.triggers(p, t_target) if x <= t_target]
        last = trig[-1] if trig else -99
        if last > self._last_trigger + 0.05:
            self.beat_index += 1
            self._last_trigger = last
        env = math.exp(-(t_target - last) / decay) if last > 0 else 0.0
        if p.get("trigger") == "vocals":
            env = max(env, self.an.level["vocals"])     # singing is continuous, so follow it
        col = pal[self.beat_index % len(pal)]
        lv = self.an.level
        if style == "color_step":
            return [_scale(col, 1.0)] * n
        if style == "burst":
            half = n / 2
            width = env * half
            return [_scale(col, 1.0 if abs(i - half + 0.5) <= width else floor) for i in range(n)]
        if style == "spectrum":
            bands = [lv["bass"], lv["snare"], lv["hats"]]
            out = []
            for i in range(n):
                b = min(2, i * 3 // n)
                out.append(_scale(pal[b % len(pal)], floor + (1 - floor) * C.apply_gamma(bands[b], 1.6)))
            return out
        if style == "energy":
            v = floor + (1 - floor) * C.apply_gamma(self.an.volume, 1.5)
            return [_scale(C.sample(pal, i / n + time.perf_counter() * 0.1 * (0.3 + self.an.volume)), v) for i in range(n)]
        # flash
        return [_scale(col, floor + (1 - floor) * env)] * n


def daylight_curve(now: time.struct_time, wake: str, bed: str) -> tuple[int, int]:
    """Kelvin + brightness for the time of day, warm and dim at both ends."""
    h = now.tm_hour + now.tm_min / 60
    w = int(wake[:2]) + int(wake[3:5]) / 60
    b = int(bed[:2]) + int(bed[3:5]) / 60
    if b <= w:
        b += 24
        if h < w:
            h += 24
    if not (w <= h <= b):
        return 2500, 10
    x = (h - w) / (b - w)
    s = math.sin(math.pi * x) ** 0.8
    return int(2500 + (6000 - 2500) * s), int(20 + 80 * s)


# ---------------------------------------------------------------------------

class LiveEngine:
    """Runs one live mode at a time on a set of strips."""

    def __init__(self, core):
        self.core = core
        self.mode: str | None = None
        self.params: dict = {}
        self.targets: list[str] = []
        self.task: asyncio.Task | None = None
        self.screen = ScreenSource()
        self.music = MusicSource()
        self.error: str | None = None
        self.status: str = ""
        self._lock = asyncio.Lock()

    def public(self):
        d = {"mode": self.mode, "params": self.params, "targets": self.targets,
             "error": self.error, "status": self.status}
        if self.mode == "music":
            d["music"] = self.music.an.snapshot()
            d["music"]["device"] = self.music.cap.device_name
            if self.music.cap.error:
                d["error"] = self.music.cap.error
        return d

    async def start(self, mode: str, params: dict, targets: list[str]):
        # Serialised: two quick Start presses must never leave an orphaned task.
        async with self._lock:
            await self._stop_locked()
            self.mode, self.params, self.targets, self.error = mode, params, targets, None
            if mode == "music":
                try:
                    self.music.start(params)
                except RuntimeError as e:
                    self.error = str(e)
                    self.mode = None
                    raise
            runner = {"daylight": self._daylight, "playlist": self._playlist}.get(mode, self._frames)
            self.task = asyncio.create_task(self._guard(runner))

    def update(self, params: dict):
        self.params.update(params)

    async def _guard(self, run):
        try:
            await run()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.error = str(e)
            self.mode = None

    async def stop(self, strip_ids: list[str] | None = None):
        if strip_ids is not None and not set(strip_ids) & set(self.targets):
            return
        if asyncio.current_task() is self.task:
            return   # a playlist step applying a scene must not cancel itself
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self):
        t, self.task = self.task, None
        if t and not t.done():
            t.cancel()
            with contextlib.suppress(BaseException):
                await t
        if self.mode == "music":
            self.music.stop()
        self.mode = None
        self.status = ""
        for s in self.core.streamers.values():
            s.reset()

    def _strips(self):
        return [self.core.strips[i] for i in self.targets if i in self.core.strips and self.core.strips[i].connected]

    async def _frames(self):
        mode = self.mode
        for s in self._strips():           # some lights only accept a pattern after the running effect is cleared
            if s.apply_mode == "clear" and "zones" in s.caps:
                try:
                    await self.core._clear_effects(s, 50)
                except Exception:
                    pass
        while True:
            p = self.params
            strips = self._strips()
            if not strips:
                await asyncio.sleep(0.5)
                continue
            now = time.perf_counter()
            # Render for the moment the frame will actually show on the strip.
            rtts = [sorted(s.rtt)[len(s.rtt) * 3 // 4] for s in strips if s.rtt] or [0.3]
            lead = max(rtts) * 0.85 + self.core.store.data["prefs"].get("sync_offset_ms", 0) / 1000
            t = now + lead
            join = (self.core.store.data["prefs"].get("join_strips") and len(strips) > 1
                    and all("zones" in s.caps for s in strips))
            sizes = [s.segments for s in strips]

            def render(n):
                if mode == "animation":
                    return ANIMATIONS.get(p.get("animation", "flow"), anim_flow)(t, n, p)
                if mode == "screen":
                    return self.screen.render(t, n, p)
                return self.music.render(t, n, p)

            trans = None
            if mode == "music" and p.get("style", "flash") in ("flash", "burst"):
                # quick attack, let the strip fade out the decay on its own
                trans = 40 if self.music._prev_env < 0.5 else int(min(900, p.get("decay_ms", 350)))
            frames = []
            if join:
                full = render(sum(sizes))
                k = 0
                for n in sizes:
                    frames.append(full[k:k + n])
                    k += n
            else:
                one = render(max(sizes))
                # Strips get their part of the picture; one-zone lights get all of it (averaged later).
                frames = [one[:n] if "zones" in s.caps else one for s, n in zip(strips, sizes)]
            if mode == "music":
                lt = self.music._last_trigger
                self.music._prev_env = math.exp(-(t - lt) / max(0.05, p.get("decay_ms", 350) / 1000)) if lt > 0 else 0
            for s, f in zip(strips, frames):
                self.core.streamer(s).submit(f, trans)
            self.status = "running"
            await asyncio.sleep(1 / 30 if mode == "music" else 0.1)

    async def _daylight(self):
        p = self.params
        while True:
            k, b = daylight_curve(time.localtime(), p.get("wake", "07:00"), p.get("bed", "23:00"))
            b = int(b * float(p.get("max_brightness", 100)) / 100)
            self.status = f"{k} K at {b}%"
            for s in self._strips():
                try:
                    if "white" in s.caps:
                        await self.core.backend.white(s, k, max(1, b))
                        s.mode = "white"
                    else:
                        await self.core.backend.brightness(s, max(1, b))
                    s.brightness = b
                except Exception:
                    pass
            await asyncio.sleep(60)

    async def _playlist(self):
        pl = next((x for x in self.core.store.data["playlists"] if x["id"] == self.params.get("playlist_id")), None)
        if not pl or not pl.get("steps"):
            raise RuntimeError("That playlist is empty")
        steps = list(pl["steps"])
        i = 0
        while True:
            if pl.get("shuffle") and i % len(steps) == 0:
                random.shuffle(steps)
            step = steps[i % len(steps)]
            scene = self.core.find_scene(step.get("scene_id"))
            if scene:
                self.status = f"Playing: {scene['name']}"
                await self.core.apply_scene(scene, self.targets, from_engine=True)
            await asyncio.sleep(max(5, int(step.get("seconds", 60))))
            i += 1
