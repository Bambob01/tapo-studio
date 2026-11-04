"""Synthetic full mixes with known drum and vocal timings, for scoring the detector.

These are much harder than the simple tracks in test_audio.py: sustained and
moving bass, pads, a sung vocal with vibrato, and a loud limited master.
"""

import numpy as np

SR = 44100


def _place(sig, t0, x):
    i = int(t0 * SR)
    j = min(len(sig), i + len(x))
    if 0 <= i < len(sig):
        sig[i:j] += x[:j - i]


def _kick(rng, gain):
    t = np.arange(int(0.2 * SR)) / SR
    body = np.sin(2 * np.pi * np.cumsum(48 + 110 * np.exp(-t * 35)) / SR) * np.exp(-t * 14)
    click = rng.standard_normal(len(t)) * np.exp(-t * 400) * 0.25
    return gain * (body + click)


def _snare(rng, gain):
    t = np.arange(int(0.18 * SR)) / SR
    noise = np.diff(rng.standard_normal(len(t) + 1)) * 0.6 * np.exp(-t * 22)
    return gain * (noise + 0.5 * np.sin(2 * np.pi * 195 * t) * np.exp(-t * 30))


def _hat(rng, gain):
    t = np.arange(int(0.05 * SR)) / SR
    x = np.diff(np.diff(rng.standard_normal(len(t) + 2)))
    return gain * 0.35 * x * np.exp(-t * 110)


def _note(f, dur, gain, harmonics=6):
    t = np.arange(int(dur * SR)) / SR
    env = np.minimum(1, t / 0.012) * np.minimum(1, (t[-1] - t) / 0.02 + 1e-3)
    return gain * env * sum(np.sin(2 * np.pi * f * h * t) / h ** 1.5 for h in range(1, harmonics + 1))


def _syllable(f0, dur, gain, rng):
    """A sung note: pitch with vibrato, many harmonics shaped by two formants."""
    t = np.arange(int(dur * SR)) / SR
    pitch = f0 * (1 + 0.012 * np.sin(2 * np.pi * 5.5 * t + rng.uniform(0, 6)))
    ph = 2 * np.pi * np.cumsum(pitch) / SR
    f1, f2 = rng.uniform(500, 800), rng.uniform(1100, 2200)
    x = np.zeros_like(t)
    for h in range(1, 16):
        fh = f0 * h
        amp = np.exp(-((fh - f1) / 300) ** 2) + 0.6 * np.exp(-((fh - f2) / 450) ** 2) + 0.02
        x += amp * np.sin(h * ph)
    env = np.minimum(1, t / 0.03) * np.minimum(1, (t[-1] - t) / 0.06 + 1e-3)
    return gain * env * x


STYLES = {
    # kick pattern and snare pattern in 8th notes over one bar; hat step in 8ths
    "pop": dict(bpm=104, kicks=[0, 3, 4], snares=[2, 6], hat=1, bass=0.30, sub=False),
    "edm": dict(bpm=128, kicks=[0, 2, 4, 6], snares=[2, 6], hat=1, bass=0.34, sub=True),
    "rock": dict(bpm=140, kicks=[0, 4, 5], snares=[2, 6], hat=1, bass=0.30, sub=False),
    "hiphop": dict(bpm=88, kicks=[0, 3, 5], snares=[2, 6], hat=0.5, bass=0.42, sub=True),
}


def mix(style="pop", dur=24.0, seed=0, vocals=True, master=True):
    """Returns (signal, truth) where truth maps band name to onset times."""
    s = STYLES[style]
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    sig = np.zeros(n)
    e = 30 / s["bpm"]                    # one 8th note
    bar = 8 * e
    truth = {"bass": [], "snare": [], "hats": [], "vocals": [], "sung": []}
    roots = [55.0, 43.65, 65.41, 49.0]
    b = 0
    for t0 in np.arange(0.2, dur - bar, bar):
        root = roots[b % 4]
        b += 1
        for k in s["kicks"]:
            _place(sig, t0 + k * e, _kick(rng, 0.8))
            truth["bass"].append(t0 + k * e)
        for k in s["snares"]:
            _place(sig, t0 + k * e, _snare(rng, 0.55))
            truth["snare"].append(t0 + k * e)
        for k in np.arange(0, 8, s["hat"]):
            _place(sig, t0 + k * e, _hat(rng, 0.3 * rng.uniform(0.7, 1.0)))
            truth["hats"].append(t0 + k * e)
        if s["sub"]:                     # one long sustained sub note per bar
            _place(sig, t0, _note(root, bar, s["bass"], 2))
        else:                            # a moving 8th-note bassline
            for k in range(8):
                _place(sig, t0 + k * e, _note(root * [1, 1, 1.5, 1][k % 4], e, s["bass"], 5))
        for f in (root * 4, root * 5, root * 6):   # pad chord
            _place(sig, t0, _note(f, bar, 0.05, 4))
    if vocals:
        t, scale = 0.2 + 2 * bar, [220, 247, 262, 294, 330, 392]
        while t < dur - 3:               # phrases of a few syllables, then a rest
            phrase = int(rng.integers(3, 7))
            truth["vocals"].append(t)
            for _ in range(phrase):
                d = float(rng.uniform(0.2, 0.5))
                truth["sung"].append(t)
                _place(sig, t, _syllable(scale[rng.integers(len(scale))], d, 0.09, rng))
                t += d
            t += float(rng.uniform(0.9, 2.0))
    sig += 0.003 * rng.standard_normal(n)
    if master:                           # loud limited master, like a commercial track
        sig = np.tanh(sig * 1.8) / np.tanh(1.8)
    return sig.astype(np.float32), {k: np.array(v) for k, v in truth.items()}


def score(hits, truth, since, tol=0.05):
    """(recall, precision) of hit times against true times after `since`."""
    hits = np.array([h for h in hits if h > since])
    truth = truth[truth > since]
    if not len(truth):
        return 0.0, 0.0
    if not len(hits):
        return 0.0, 0.0
    recall = float(np.mean([np.min(np.abs(hits - k)) < tol for k in truth]))
    precision = float(np.mean([np.min(np.abs(truth - h)) < tol for h in hits]))
    return recall, precision


def fast_kicks(bpm=170, step=0.25, dur=16.0, seed=0, bassline=True):
    """Metal-style double kicks: a kick every `step` beats, over a held bass note and hats."""
    rng = np.random.default_rng(seed)
    sig = np.zeros(int(dur * SR))
    times = np.arange(0.2, dur - 0.5, 60 / bpm * step)
    for t0 in times:
        _place(sig, t0, _kick(rng, 0.8)[:int(0.09 * SR)] * np.linspace(1, 0, int(0.09 * SR)) ** 0.5)
    for t0 in np.arange(0.2, dur - 0.5, 60 / bpm):
        _place(sig, t0, _hat(rng, 0.3))
    if bassline:
        _place(sig, 0, _note(55.0, dur, 0.25, 4))
    sig = np.tanh(sig * 1.8) / np.tanh(1.8) + 0.003 * rng.standard_normal(len(sig))
    return sig.astype(np.float32), times
