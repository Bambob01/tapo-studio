"""Smart music analysis: per-band hit detection, auto-gain and tempo lock.

No machine learning needed. Each hop (~23 ms) we:

1. FFT the latest window and split it into bands:
   bass/kick 40-150 Hz, snare crack 1.5-5 kHz, hats 6-14 kHz, vocals 300-3400 Hz.
2. Measure *spectral flux* per band, which is how much louder the band got
   since the last hop. A steady bass line has low flux and a kick drum has a
   spike.
3. Fire a hit when the flux jumps above the band's own recent average by a
   sensitivity-scaled margin (adaptive threshold, so quiet and loud songs
   both work), with a per-band refractory period. The attack must also be big
   next to the band's recent hits, which rejects bass notes and stray clicks.
4. Autocorrelate the drum onset envelope over the last ~6 s to find the tempo,
   then run a small phase-locked loop on the hits so we can *predict* the next
   beat. That prediction lets the app send the pulse early enough to arrive on
   time, because the strip takes ~0.3 s to apply each command.
"""

from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

BANDS = {
    "bass": [(40, 150)],
    "snare": [(1500, 5000)],
    "hats": [(6000, 14000)],
    "vocals": [(300, 3400)],
}
DRUMS = ("bass", "snare", "hats")
REFRACTORY = {"bass": 0.18, "snare": 0.15, "hats": 0.09, "vocals": 0.18}
# How big a hit must be next to the band's biggest recent hit, at sensitivity 0
# and 1. Real drums are the loudest attacks in their band, so this is what
# separates a kick from a bass note, or a snare from a kick's click.
STRENGTH = {"bass": (0.75, 0.3), "snare": (0.75, 0.3), "hats": (0.25, 0.05)}


class BeatAnalyzer:
    def __init__(self, samplerate: int = 44100, hop: int = 1024, window: int = 2048):
        self.sr = samplerate
        self.hop = hop
        self.win = window
        self.window_fn = np.hanning(window).astype(np.float32)
        freqs = np.fft.rfftfreq(window, 1 / samplerate)
        self.masks = {
            b: np.any([(freqs >= lo) & (freqs < hi) for lo, hi in rngs], axis=0)
            for b, rngs in BANDS.items()
        }
        self.attack_peak = {b: 1e-6 for b in DRUMS}
        self.attack_decay = 0.5 ** (hop / samplerate / 3.0)   # halves every 3 s
        # Fast kick detector: works on the waveform at ~1.4 kHz, because the 46 ms
        # FFT window can't separate kicks that land 75-125 ms apart.
        self.dec = 32
        fs = samplerate / self.dec
        w0 = 2 * np.pi * 85 / fs
        alpha = np.sin(w0) / (2 * 0.8)                # band-pass around 85 Hz
        self.bq = (alpha / (1 + alpha), -alpha / (1 + alpha),
                   -2 * np.cos(w0) / (1 + alpha), (1 - alpha) / (1 + alpha))
        self.bq_state = [0.0, 0.0, 0.0, 0.0]
        self.fs = fs
        self.low_sq = np.zeros(int(0.08 * fs))        # recent squared band signal
        self.low_env = np.zeros(int(0.08 * fs))
        self.fast_peak = 1e-6
        self.fast_armed = True
        self.fast_frac, self.fast_gate, self.fast_window = 0.45, 1.6, 0.17
        self.fast_start, self.fast_run = 0.99, False
        self.vocal_share = 0.2
        self.vocal_bg = None                         # slow per-frequency background
        self.vocal_recent: deque[float] = deque(maxlen=5)
        self.vocal_peak = 1e-6
        self.vocal_on = False
        self.vocal_quiet = 0.0
        self.buf = np.zeros(window, dtype=np.float32)
        self.prev = {b: None for b in BANDS}
        hist = int(1.2 * samplerate / hop)            # ~1.2 s of flux history
        self.flux_hist = {b: deque(maxlen=hist) for b in BANDS}
        self.peak = {b: 1e-6 for b in BANDS}
        self.level = {b: 0.0 for b in BANDS}
        self.last_hit = {b: -1.0 for b in BANDS}
        self.recent_energy = {b: deque(maxlen=4) for b in BANDS}
        self.hits: deque[tuple[float, str, float]] = deque(maxlen=64)
        self.sensitivity = 0.5                       # 0 = only huge hits, 1 = everything
        self.rms_peak = 1e-4
        self.volume = 0.0

        rate = samplerate / hop                      # onset envelope sample rate (Hz)
        self.env_rate = rate
        self.env = deque(maxlen=int(8 * rate))       # drum onset envelope, 8 s
        self.bpm = 0.0
        self.confidence = 0.0
        self.period = 0.0
        self.phase_t = 0.0                           # time of a beat on the locked grid
        self._hops = 0
        self.lock = threading.Lock()

    # -- input ------------------------------------------------------------
    def feed(self, mono: np.ndarray, t: float | None = None):
        """Feed any number of samples. `t` = capture time of the last sample."""
        t = time.perf_counter() if t is None else t
        n = len(mono)
        for i in range(0, n - self.hop + 1, self.hop):
            chunk = mono[i:i + self.hop]
            self.buf = np.concatenate([self.buf[self.hop:], chunk.astype(np.float32)])
            self._process(t - (n - i - self.hop) / self.sr)

    def _process(self, t: float):
        x = self.buf * self.window_fn
        rms = float(np.sqrt(np.mean(self.buf ** 2)))
        self.rms_peak = max(rms, self.rms_peak * 0.9995)
        self.volume = min(1.0, rms / (self.rms_peak + 1e-9))
        silent = rms < 1e-4
        mag = np.abs(np.fft.rfft(x))
        logmag = np.log1p(mag * 50)
        k = 2.6 - 2.0 * self.sensitivity            # threshold in std-devs above mean
        with self.lock:
            onset = 0.0
            for b in DRUMS:
                mask = self.masks[b]
                band = logmag[mask]
                energy = float(np.mean(mag[mask]))
                self.peak[b] = max(energy, self.peak[b] * 0.9993)
                self.level[b] = 0.6 * self.level[b] + 0.4 * min(1.0, energy / (self.peak[b] + 1e-9))
                prev = self.prev[b]
                flux = 0.0 if prev is None else float(np.sum(np.maximum(band - prev, 0)) / len(band))
                self.prev[b] = band
                re = self.recent_energy[b]
                base = sum(re) / len(re) if re else 0.0
                attack = max(0.0, energy - base) if re else 0.0
                self.attack_peak[b] = max(attack, self.attack_peak[b] * self.attack_decay)
                hist = self.flux_hist[b]
                if len(hist) > 10 and not silent:
                    arr = np.fromiter(hist, float)
                    # Median-based, so the hits themselves don't raise the bar.
                    med = float(np.median(arr))
                    thr = med + k * 1.4826 * float(np.median(np.abs(arr - med))) + 1e-3
                    # The analysis window spots a hit ~18 ms after it starts.
                    th = t - 0.018
                    lo, hi = STRENGTH[b]
                    big = attack > (lo + (hi - lo) * self.sensitivity) * self.attack_peak[b]
                    # Percussive gate: a real hit makes the band suddenly *louder*.
                    rise = energy / (base + 1e-9) if re else 0.0
                    gate = 1.3 - 0.3 * self.sensitivity
                    if flux > thr and big and rise > gate and th - self.last_hit[b] > REFRACTORY[b]:
                        strength = min(1.0, 0.4 + 0.6 * attack / self.attack_peak[b])
                        self.last_hit[b] = th
                        self.hits.append((th, b, strength))
                        if b == "bass":
                            self._pll(th)
                hist.append(flux)
                re.append(energy)
                onset += attack / self.attack_peak[b]
            self.env.append(onset)
            self._vocals(mag, t, silent)
            self._fast_bass(self.buf[-self.hop:], t, silent)
            self._hops += 1
            if self._hops % 20 == 0:
                self._tempo()

    # -- fast bass --------------------------------------------------------
    def _fast_bass(self, chunk: np.ndarray, t: float, silent: bool):
        """Fills in fast runs. The main detector finds the kicks; this only adds a hit
        that lands too soon after another for the main detector to separate, so a
        bass note on its own can never trigger it."""
        d = chunk[:len(chunk) // self.dec * self.dec].reshape(-1, self.dec).mean(axis=1)
        b0, b2, a1, a2 = self.bq
        x1, x2, y1, y2 = self.bq_state
        out = np.empty(len(d))
        for i, x in enumerate(d):
            y = b0 * x + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, float(x), y1, y
            out[i] = y
        self.bq_state = [x1, x2, y1, y2]
        width = int(0.016 * self.fs)                  # about one cycle of a low note
        back, skip = int(0.045 * self.fs), int(0.006 * self.fs)
        decay = 0.5 ** (1 / self.fs / 3.0)
        frac = self.fast_frac + 0.2 * (0.5 - self.sensitivity)
        gate = self.fast_gate - 0.3 * (self.sensitivity - 0.5)
        for i, y in enumerate(out):
            self.low_sq = np.roll(self.low_sq, -1)
            self.low_sq[-1] = y * y
            env = float(np.sqrt(self.low_sq[-width:].mean()))
            self.low_env = np.roll(self.low_env, -1)
            self.low_env[-1] = env
            base = float(self.low_env[-back:-skip].min())
            attack = env - base
            self.fast_peak = max(attack, self.fast_peak * decay)
            # The first fill-in of a run must be as big as a full kick; once a run is going, less will do.
            need = (frac if self.fast_run else max(frac, self.fast_start)) * self.fast_peak
            if attack < 0.5 * need:
                self.fast_armed = True
            if self.fast_armed and not silent and attack > need and env > gate * base:
                th = t - (len(out) - 1 - i) / self.fs - 0.012
                gap = th - self.last_hit["bass"]
                self.fast_run = self.fast_run and gap < self.fast_window
                if 0.06 < gap < self.fast_window:
                    self.fast_armed = False
                    self.fast_run = True
                    self.last_hit["bass"] = th
                    self.hits.append((th, "bass", min(1.0, 0.4 + 0.6 * attack / self.fast_peak)))

    # -- vocals -----------------------------------------------------------
    def _vocals(self, mag: np.ndarray, t: float, silent: bool):
        """Find singing: pitched peaks in the voice range that aren't steady.

        Drums are noise rather than pitched peaks, and pads and held synths
        sink into the slow background. This can't tell a voice from a lead
        guitar or synth melody, because they look the same in a mono mix."""
        m = mag[self.masks["vocals"]]
        if self.vocal_bg is None:
            self.vocal_bg = np.zeros_like(m)
        smooth = np.convolve(m, np.ones(9) / 9, "same")
        peaks = np.maximum(m - 1.5 * smooth, 0)                   # pitched, not noise
        moved = np.maximum(peaks - self.vocal_bg, 0)              # new, not steady
        self.vocal_bg += (peaks - self.vocal_bg) * 0.02
        self.vocal_recent.append(float(moved.sum()))
        v = float(np.median(self.vocal_recent))                   # a drum hit is too brief to count
        self.vocal_peak = max(v, self.vocal_peak * 0.999)
        share = v / (float(m.sum()) + 1e-9)            # how much of the voice range is new pitched sound
        ratio = 0.0 if silent or share < self.vocal_share else v / (self.vocal_peak + 1e-9)
        self.level["vocals"] = 0.7 * self.level["vocals"] + 0.3 * min(1.0, ratio)
        on = 0.5 - 0.35 * self.sensitivity
        floor = min(self.vocal_recent)
        self.vocal_quiet = self.vocal_quiet + self.hop / self.sr if ratio < on * 0.5 else 0.0
        self.vocal_on = self.vocal_quiet < 0.3 and (self.vocal_on or ratio > on)
        th = t - 0.05
        if ratio > on and v > 1e-3 and v > 1.3 * floor and th - self.last_hit["vocals"] > REFRACTORY["vocals"]:
            self.last_hit["vocals"] = th
            self.hits.append((th, "vocals", min(1.0, 0.4 + ratio)))

    # -- tempo ------------------------------------------------------------
    def _tempo(self):
        if len(self.env) < self.env_rate * 2.5:
            return
        e = np.fromiter(self.env, float)
        e = e - e.mean()
        if not e.any():
            return
        ac = np.correlate(e, e, "full")[len(e) - 1:]
        lo, hi = int(self.env_rate * 60 / 180), int(self.env_rate * 60 / 70)   # 70-180 BPM
        seg = ac[lo:hi]
        if len(seg) < 3 or ac[0] <= 0:
            return
        # Weight towards the 100-130 BPM range most music lives in, to avoid
        # locking onto half or double tempo.
        lags = np.arange(lo, hi)
        # A true beat also lines up with the bar, so reward lags that repeat
        # at 2x and 4x. Off-beat patterns only match at one lag.
        seg = np.maximum(seg, 0) + 0.5 * np.maximum(ac[np.minimum(lags * 2, len(ac) - 1)], 0) \
            + 0.5 * np.maximum(ac[np.minimum(lags * 4, len(ac) - 1)], 0)
        bpms = 60 * self.env_rate / lags
        weight = np.exp(-0.5 * ((np.log2(bpms / 120)) / 0.6) ** 2)
        idx = int(np.argmax(seg * weight))
        lag = lo + idx
        if 0 < idx < len(seg) - 1:   # parabolic refinement
            a, b, c = seg[idx - 1], seg[idx], seg[idx + 1]
            denom = a - 2 * b + c
            if denom:
                lag += 0.5 * (a - c) / denom
        li = int(round(lag))
        conf = float(ac[li] / ac[0]) * len(e) / (len(e) - li)   # undo the shorter overlap at long lags
        bpm = 60 * self.env_rate / lag
        self.confidence = 0.8 * self.confidence + 0.2 * max(0.0, min(1.0, conf * 2))
        self.bpm = bpm if not self.bpm else 0.8 * self.bpm + 0.2 * bpm
        self.period = 60 / self.bpm

    def _pll(self, t: float):
        """Nudge the predicted beat grid towards each real bass hit."""
        if not self.period:
            self.phase_t = t
            return
        n = round((t - self.phase_t) / self.period)
        predicted = self.phase_t + n * self.period
        err = t - predicted
        if abs(err) < self.period * 0.35:
            self.phase_t = predicted + 0.3 * err
        else:
            self.phase_t = t            # way off: re-anchor

    # -- output -----------------------------------------------------------
    def locked(self, min_conf: float = 0.35) -> bool:
        return self.period > 0 and self.confidence >= min_conf

    def beat_times(self, t_from: float, t_to: float) -> list[float]:
        """Predicted beat times on the locked grid within [t_from, t_to]."""
        if not self.period:
            return []
        n0 = int(np.ceil((t_from - self.phase_t) / self.period))
        out, k = [], n0
        while True:
            bt = self.phase_t + k * self.period
            if bt > t_to:
                return out
            out.append(bt)
            k += 1

    def recent_hits(self, band: str, since: float) -> list[tuple[float, float]]:
        with self.lock:
            return [(t, s) for t, b, s in self.hits
                    if t >= since and (b == band or (band == "any" and b in DRUMS))]

    def snapshot(self) -> dict:
        with self.lock:
            now = time.perf_counter()
            return {
                "levels": {b: round(v, 3) for b, v in self.level.items()},
                "volume": round(self.volume, 3),
                "bpm": round(self.bpm, 1) if self.confidence > 0.2 else None,
                "confidence": round(self.confidence, 2),
                "locked": self.locked(),
                "hits": {b: round(now - self.last_hit[b], 2) for b in BANDS},
                "singing": self.vocal_on,
            }


def demo_track(sr: int, bpm: float = 124.0, bars: int = 2) -> np.ndarray:
    """A short synthetic loop (kick, snare, hats, bassline) for trying the music
    mode without any audio set-up. Pure numpy; loops seamlessly."""
    beat = 60.0 / bpm
    n = int(sr * beat * 4 * bars)
    out = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(7)

    def add(t0, sig):
        i = int(t0 * sr)
        j = min(n, i + len(sig))
        if i < n:
            out[i:j] += sig[:j - i]

    kt = np.arange(int(0.16 * sr)) / sr
    kick = (0.9 * np.sin(2 * np.pi * np.cumsum(50 + 90 * np.exp(-kt * 30)) / sr) * np.exp(-kt * 16)).astype(np.float32)
    st = np.arange(int(0.12 * sr)) / sr
    snare = (0.4 * (rng.standard_normal(len(st)) * 0.7 + np.sin(2 * np.pi * 190 * st)) * np.exp(-st * 25)).astype(np.float32)
    ht = np.arange(int(0.04 * sr)) / sr
    hat = (0.18 * rng.standard_normal(len(ht)) * np.exp(-ht * 90)).astype(np.float32)
    for b in range(4 * bars):
        add(b * beat, kick)
        if b % 2 == 1:
            add(b * beat, snare)
        add(b * beat + beat / 2, hat)
    t = np.arange(n) / sr
    out += (0.12 * np.sin(2 * np.pi * 55 * t)).astype(np.float32)
    return out


class AudioCapture:
    """Records whatever is playing on the PC (WASAPI loopback via `soundcard`).

    source="demo" plays a built-in synthetic loop instead, which needs no audio
    device and no extra packages."""

    def __init__(self, analyzer: BeatAnalyzer):
        self.an = analyzer
        self.running = False
        self.error: str | None = None
        self.thread: threading.Thread | None = None
        self.device_name = None

    def start(self, source: str = "loopback"):
        if self.running:
            return
        if source != "demo":
            try:
                import soundcard as sc  # noqa: F401
            except Exception as e:
                self.error = ("Music mode needs the 'soundcard' package: run install-extras.bat on Windows, "
                              f"or pip install \"tapo-studio[music]\", then restart. ({e})")
                raise RuntimeError(self.error)
        self.running = True
        self.error = None
        self.thread = threading.Thread(target=self._run, args=(source,), daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _run_demo(self):
        """Feed the analyser a looping synthetic track, paced in real time."""
        track = demo_track(self.an.sr)
        self.device_name = "Demo track (124 BPM)"
        hop, pos = self.an.hop, 0
        next_t = time.perf_counter()
        try:
            while self.running:
                idx = (np.arange(pos, pos + hop)) % len(track)
                self.an.feed(track[idx], next_t + hop / self.an.sr)
                pos = (pos + hop) % len(track)
                next_t += hop / self.an.sr
                delay = next_t - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -1:            # fell far behind (e.g. laptop slept): resync
                    next_t = time.perf_counter()
        finally:
            self.running = False

    def _run(self, source):
        if source == "demo":
            return self._run_demo()
        import soundcard as sc
        try:
            if source == "microphone":
                mic = sc.default_microphone()
            else:
                spk = sc.default_speaker()
                mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
            self.device_name = mic.name
            with mic.recorder(samplerate=self.an.sr, channels=2, blocksize=self.an.hop) as rec:
                while self.running:
                    data = rec.record(numframes=self.an.hop)
                    mono = data.mean(axis=1) if data.ndim == 2 else data
                    self.an.feed(mono, time.perf_counter())
        except Exception as e:
            self.error = f"Audio capture stopped: {e}"
        finally:
            self.running = False
