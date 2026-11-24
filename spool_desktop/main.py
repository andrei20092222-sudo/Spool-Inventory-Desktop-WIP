"""Start the app: a local server plus your web browser as the window."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys
import threading
import urllib.request
import webbrowser

from . import __version__
from .server import MARKER, App, Server, make_handler

DEFAULT_PORT = 8765


def default_data_dir() -> Path:
    """Next to the program (portable). Falls back to the user profile when that folder is read-only."""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    folder = base / "SpoolInventory_data"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        return folder
    except OSError:
        root = os.environ.get("APPDATA") or str(Path.home())
        return Path(root) / "SpoolInventory"


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1.5) as res:
            return res.read().decode() == MARKER
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="SpoolInventory", description="Spool Inventory, a local filament inventory.")
    parser.add_argument("--data", type=Path, help="folder for the data (default: SpoolInventory_data next to the program)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--version", action="version", version=f"Spool Inventory {__version__}")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if already_running(args.port):
        print(f"Spool Inventory is already running, opening it: http://127.0.0.1:{args.port}/")
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        return 0

    data_dir = args.data or default_data_dir()
    app = App(data_dir)
    port_box = {"port": args.port}
    try:
        server = Server(("127.0.0.1", args.port), make_handler(app, lambda: port_box["port"]))
    except OSError:
        server = Server(("127.0.0.1", 0), make_handler(app, lambda: port_box["port"]))  # that port is taken by something else
    port_box["port"] = server.server_address[1]
    url = f"http://127.0.0.1:{port_box['port']}/"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Spool Inventory {__version__}")
    print(f"Open {url} in your browser (it opens by itself).")
    print(f"Your data is in: {data_dir}")
    print("Close this window or press Ctrl+C to quit. Your data is saved after every change.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while not app.stop.wait(0.5):  # a timeout keeps Ctrl+C working on Windows
            pass
    except KeyboardInterrupt:
        pass
    finally:
        app.stop.set()
        server.shutdown()
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
