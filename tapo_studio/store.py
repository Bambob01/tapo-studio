"""Settings, devices, scenes, palettes, playlists and schedules, kept in one JSON file.

The file lives in your own profile (%LOCALAPPDATA%\\TapoStudio), which other
Windows accounts can't read. Your Tapo password is additionally encrypted with
Windows DPAPI (only your Windows account can decrypt it). On other systems the
file is created readable by you only.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
from pathlib import Path

_BASE = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".local", "share")
ROOT = Path(os.environ.get("TAPO_STUDIO_HOME") or Path(_BASE) / "TapoStudio")
FILE = ROOT / "settings.json"

DEFAULTS = {
    "email": "",
    "password_enc": "",
    "broadcast": "",
    "devices": [],          # {id, ip, name, segments, reverse}
    "scenes": [],           # {id, name, kind, data}
    "palettes": [],         # user palettes {id, name, colors[hex]}; built-ins live in color.py
    "playlists": [],        # {id, name, shuffle, steps:[{scene_id, seconds}]}
    "schedules": [],        # {id, enabled, time "HH:MM", days [0-6], action {...}, targets}
    "effects": [],          # saved custom lighting effects (wire format + uid)
    "segment_effects": [],  # saved custom segment effects
    "prefs": {"theme": "auto", "max_fps": 4, "auto_transition": True, "gamma": 2.1,
              "join_strips": False, "sync_offset_ms": 0},
}


# ---- password protection ---------------------------------------------------

_ENTROPY = b"TapoStudio/credentials/v1"
_UI_FORBIDDEN = 0x1


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(BLOB), wintypes.LPCWSTR, ctypes.POINTER(BLOB), ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    inp, _b1 = blob(data)
    ent, _b2 = blob(_ENTROPY)
    out = BLOB()
    if not fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, _UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError(f"DPAPI failed ({ctypes.get_last_error()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(pw: str) -> str:
    if not pw:
        return ""
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(pw.encode(), True)).decode()
    return "b64:" + base64.b64encode(pw.encode()).decode()


def unprotect(s: str) -> str:
    if not s:
        return ""
    kind, _, body = s.partition(":")
    try:
        raw = base64.b64decode(body)
        if kind == "dpapi":
            return _dpapi(raw, False).decode()
        return raw.decode()
    except Exception:
        return ""


class Store:
    def __init__(self):
        self._lock = threading.Lock()
        self.data = json.loads(json.dumps(DEFAULTS))
        if FILE.exists():
            try:
                loaded = json.loads(FILE.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("not an object")
                for k, default in DEFAULTS.items():
                    if k in loaded and isinstance(loaded[k], type(default)):
                        self.data[k] = loaded[k]
                self.data["prefs"] = {**DEFAULTS["prefs"], **self.data.get("prefs", {})}
                for k in ("devices", "scenes", "palettes", "playlists", "schedules", "effects", "segment_effects"):
                    self.data[k] = [x for x in self.data[k] if isinstance(x, dict)]
                self.data["devices"] = [d for d in self.data["devices"] if {"id", "ip", "name"} <= d.keys()]
            except Exception:
                os.replace(FILE, FILE.with_suffix(".broken.json"))

    def save(self):
        with self._lock:
            ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = FILE.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.data, indent=1))
            os.replace(tmp, FILE)

    @property
    def password(self) -> str:
        return unprotect(self.data.get("password_enc", ""))

    def set_credentials(self, email: str, password: str | None):
        self.data["email"] = email.strip()
        if password is not None:
            self.data["password_enc"] = protect(password)
        self.save()

    def public(self) -> dict:
        d = {k: v for k, v in self.data.items() if k != "password_enc"}
        d["has_password"] = bool(self.data.get("password_enc"))
        return d
