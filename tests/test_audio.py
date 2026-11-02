"""Beat analysis on synthetic music (no audio hardware needed). Seeds are fixed, so this is deterministic."""

import time

import numpy as np
import pytest

from tapo_studio.audio import BeatAnalyzer, demo_track

SR = 44100


def kick(n_samples=int(0.15 * SR)):
    t = np.arange(n_samples) / SR
    return 0.9 * np.sin(2 * np.pi * np.cumsum(50 + 90 * np.exp(-t * 30)) / SR) * np.exp(-t * 16)


def simple_track(bpm, dur=14, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(SR * dur)) / SR
    sig = 0.15 * np.sin(2 * np.pi * 55 * t)                         # steady bass the detector must ignore
    period = 60 / bpm
    kicks = np.arange(0.3, dur - 0.3, period)
    for k in kicks:
        i = int(k * SR)
        sig[i:i + int(0.15 * SR)] += kick()
    for k in kicks + period / 2:                                     # hats on the off-beat
        i, n = int(k * SR), int(0.05 * SR)
        if i + n < len(sig):
            sig[i:i + n] += 0.2 * rng.standard_normal(n) * np.exp(-np.arange(n) / SR * 80)
    sig += 0.01 * rng.standard_normal(len(sig))
    return sig.astype(np.float32), kicks, dur


def busy_track(bpm=122, dur=16, seed=1):
    """Moving bassline + pads + kick + snare: the hard case for a bass detector."""
    rng = np.random.default_rng(seed)
    p = 60 / bpm
    t = np.arange(int(SR * dur)) / SR
    sig = np.zeros_like(t)
    notes = [41, 55, 49, 62]
    for j, st in enumerate(np.arange(0, dur, p / 2)):
        i, n = int(st * SR), int(p / 2 * SR)
        tt = np.arange(n) / SR
        env = np.minimum(1, tt / 0.03) * np.minimum(1, (tt[-1] - tt) / 0.03) * 0.35
        seg = env * np.sin(2 * np.pi * notes[j % 4] * tt)
        sig[i:i + n] += seg[:len(sig[i:i + n])]
    sig += 0.2 * np.sin(2 * np.pi * 330 * t + 3 * np.sin(2 * np.pi * 5 * t)) + 0.15 * np.sin(2 * np.pi * 440 * t)
    kicks = np.arange(0.25, dur - 0.3, p)
    for k in kicks:
        i = int(k * SR)
        sig[i:i + int(0.15 * SR)] += 0.8 * kick()
    snares = kicks[1::2]
    for k in snares:
        i, n = int(k * SR), int(0.12 * SR)
        tt = np.arange(n) / SR
        sig[i:i + n] += 0.4 * (rng.standard_normal(n) * 0.7 + np.sin(2 * np.pi * 190 * tt)) * np.exp(-tt * 25)
    sig += 0.02 * rng.standard_normal(len(sig))
    return sig.astype(np.float32), kicks, snares, dur


def run(sig, dur, sens=0.5):
    an = BeatAnalyzer(SR)
    an.sensitivity = sens
    an.feed(sig, t=dur)                      # pretend the track just finished at time `dur`
    return an


def score(hit_times, truth, since, tol=0.05):
    hits = np.array([h for h in hit_times if h > since])
    truth = truth[truth > since]
    tp = sum(np.min(np.abs(truth - h)) < tol for h in hits) if len(hits) else 0
    caught = sum(np.min(np.abs(hits - k)) < tol for k in truth) if len(hits) else 0
    return caught, len(truth), len(hits) - tp


@pytest.mark.parametrize("bpm", [95, 124, 140, 174])
def test_tempo_and_kick_timing(bpm):
    sig, kicks, dur = simple_track(bpm)
    an = run(sig, dur)
    assert abs(an.bpm - bpm) < 2.5
    assert an.locked()
    # The analyser keeps only its last 64 hits, so score the final 6 seconds.
    caught, total, false = score([h[0] for h in an.hits if h[1] == "bass"], kicks, dur - 6)
    assert caught >= total - 1 and false == 0


def test_predicted_beats_land_on_the_kicks():
    sig, kicks, dur = simple_track(128)
    an = run(sig, dur)
    pred = an.beat_times(dur - 2.5, dur - 0.5)        # beats the grid predicts inside the track
    assert pred
    assert max(min(abs(kicks - p)) for p in pred) < 0.04


@pytest.mark.parametrize("sens", [0.5, 0.7])
def test_busy_mix_bass_and_snare(sens):
    sig, kicks, snares, dur = busy_track()
    an = run(sig, dur, sens)
    bass = score([h[0] for h in an.hits if h[1] == "bass"], kicks, dur - 12)
    snare = score([h[0] for h in an.hits if h[1] == "snare"], snares, dur - 12)
    assert bass[0] >= 0.8 * bass[1] and bass[2] == 0, bass          # a moving bassline must not trigger it
    assert snare[0] >= 0.9 * snare[1] and snare[2] <= 1, snare      # kicks must not trigger the snare detector
    assert abs(an.bpm - 122) < 3


def test_silence_triggers_nothing():
    an = run(np.zeros(SR * 5, dtype=np.float32), 5)
    assert not an.hits and not an.locked()


def test_builtin_demo_track_is_detected():
    track = demo_track(SR, 124.0, bars=2)
    sig = np.tile(track, 4)
    an = run(sig, len(sig) / SR)
    assert abs(an.bpm - 124) < 3
    kinds = {h[1] for h in an.hits}
    assert {"bass", "snare"} <= kinds


def test_music_source_flashes_on_predicted_beat():
    from tapo_studio.live import MusicSource
    sig, kicks, dur = simple_track(128)
    m = MusicSource()
    now = time.perf_counter()
    m.an.feed(sig, now)
    kick_abs = now - dur + kicks
    nxt = kick_abs[-1] + 60 / 128                     # a beat that hasn't happened yet
    p = {"trigger": "bass", "style": "flash", "predict": True, "decay_ms": 300, "floor": 0.05, "palette": "Sunset"}
    before = max(m.render(nxt - 0.06, 10, p)[0])
    after = max(m.render(nxt + 0.01, 10, p)[0])
    assert after > before + 80                        # the flash arrives with the predicted beat
    for style in ("burst", "color_step", "spectrum", "energy"):
        assert len(m.render(now, 50, {**p, "style": style})) == 50
