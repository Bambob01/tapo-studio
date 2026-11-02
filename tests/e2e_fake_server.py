"""Dev tool: run the REAL (non-demo) app against the fake `tapo` library.

    python tests/e2e_fake_server.py [port]

Handy for exercising the real-hardware code path in a browser without any lights.
"""
import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TAPO_STUDIO_HOME", tempfile.mkdtemp(prefix="tapo-studio-e2e-"))

import pytest
import fake_tapo

mp = pytest.MonkeyPatch()
fake_tapo.install(mp)
fake_tapo.announce("192.168.1.50", "L930-5(EU)", "AA:BB:CC:DD:EE:50")
fake_tapo.announce("192.168.1.51", "L530E", "AA:BB:CC:DD:EE:51")
fake_tapo.announce("192.168.1.60", "P110", "AA:BB:CC:DD:EE:60")

from tapo_studio import effects as fx
if os.environ.get("FAKE_IGNORE_FIXED_ID"):
    fake_tapo.IGNORE_IDS.add(fx.FRAME_ID)        # a strip that ignores the usual effect id
if os.environ.get("FAKE_NEWER_FIRMWARE"):
    fake_tapo.CUSTOM_LIGHTING_IGNORED[0] = True   # like firmware 1.4.x: custom lighting effects are never applied
if os.environ.get("FAKE_NEEDS_CLEAR"):
    fake_tapo.NEEDS_CLEAR[0] = True              # a strip that ignores patterns while another effect is running
if os.environ.get("FAKE_IGNORE_ALL"):
    fake_tapo.IGNORE_ALL[0] = True               # a strip that shows nothing at all

from tapo_studio.__main__ import main

sys.argv = ["tapo-studio", "--no-browser", "--port", sys.argv[1] if len(sys.argv) > 1 else "8850"]
main()
