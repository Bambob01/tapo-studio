"""Start Tapo Studio:  python -m tapo_studio [--mock] [--port 8765] [--no-browser]"""

import argparse
import logging
import socket
import sys
import threading
import webbrowser


from . import __version__


def free_port(start: int) -> int:
    for p in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("No free port found")


def main():
    ap = argparse.ArgumentParser(description="Tapo Studio - advanced control for Tapo L920/L930 light strips")
    ap.add_argument("--mock", action="store_true", help="demo mode with pretend strips (no hardware needed)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--version", action="version", version=f"tapo-studio {__version__}")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    if sys.version_info < (3, 11):
        raise SystemExit("Tapo Studio needs Python 3.11 or newer.")

    if args.mock:
        # Keep demo strips out of your real settings.
        import os
        from pathlib import Path
        base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".local", "share")
        os.environ.setdefault("TAPO_STUDIO_HOME", str(Path(base) / "TapoStudio-demo"))

    import uvicorn

    from .core import Core
    from .server import build_app

    import secrets
    port = free_port(args.port)
    token = secrets.token_urlsafe(32)
    core = Core(mock=args.mock)
    app = build_app(core, port, token)
    # The key travels only in the URL fragment, which the browser never sends
    # to any server, so nothing else on this PC can fetch it from the app.
    url = f"http://127.0.0.1:{port}/#k={token}"
    print(f"\n  Tapo Studio {__version__} is running" + (" in DEMO mode (no real strips)" if core.mock else ""))
    print("  Your browser should open automatically. If you close the tab, open this link again:")
    print(f"\n    {url}\n")
    print("  (Keep that link private - it controls your lights while this window is open.)")
    print("  Close this window to stop Tapo Studio.\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # 127.0.0.1 only: never reachable from other devices on the network.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", server_header=False,
                proxy_headers=False, ws="none", limit_concurrency=64, h11_max_incomplete_event_size=16384)


if __name__ == "__main__":
    main()
