"""The local web server: serves the panel and answers its commands. Only reachable from this computer."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import queue
import threading
from typing import Any
from urllib.parse import urlsplit

from . import __version__, hastub

hastub.install()

from .core import manager as core_manager  # noqa: E402  (needs the stand-in modules first)
from .core.const import SIGNAL_INVENTORY_CHANGED  # noqa: E402
from . import api  # noqa: E402

core_manager.SAVE_DELAY = 0.3  # save almost at once: the app is closed by closing a window
core_manager.DEFAULT_SETTINGS["locations"] = []  # the integration's sample locations are not wanted here

_LOGGER = logging.getLogger(__name__)
_WEB = Path(__file__).parent / "web"
_STATIC = {
    "panel.js": "text/javascript; charset=utf-8", "shim.js": "text/javascript; charset=utf-8",
    "catalog.json": "application/json; charset=utf-8", "icon.png": "image/png",
}
MARKER = "spool-inventory-desktop"


class App:
    """The inventory plus the list of browser pages that are following it."""

    def __init__(self, data_dir: Path) -> None:
        self.hass = hastub.Hass(data_dir)
        self.manager = core_manager.SpoolManager(self.hass)
        hastub.run(self.manager.async_load())
        self.clients: list[queue.Queue[str]] = []
        self.hass._listeners.setdefault(SIGNAL_INVENTORY_CHANGED, []).append(self._changed)
        self.stop = threading.Event()
        threading.Thread(target=self._hourly, daemon=True).start()

    def _hourly(self) -> None:
        """Keep maintenance tasks up to date (overdue marks) while the app stays open."""
        while not self.stop.wait(3600):
            try:
                with self.hass.lock:
                    self.manager.equipment.check_tasks()
            except Exception:  # never let the timer die
                _LOGGER.exception("Hourly check failed")

    def snapshot(self) -> str:
        return json.dumps(api.payload(self.manager))

    def _changed(self) -> None:  # called with the lock held, from the thread that changed something
        text = self.snapshot()
        for client in list(self.clients):
            client.put(text)

    def command(self, name: str, msg: dict[str, Any]) -> Any:
        with self.hass.lock:
            return api.handle(self.manager, hastub.run, name, msg)

    def close(self) -> None:
        with self.hass.lock:
            hastub.run(self.manager.async_flush())
        hastub.flush_pending(self.hass)


def make_handler(app: App, port_getter) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "SpoolInventory"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet console
            _LOGGER.debug(fmt, *args)

        # -- protection: only this computer, only our own page --------------------------------------------------
        def _local(self) -> bool:
            port = port_getter()
            host = (self.headers.get("Host") or "").lower()
            if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return False  # blocks DNS rebinding tricks
            origin = self.headers.get("Origin")
            return origin is None or origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

        def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: Any) -> None:
            self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            if not self._local():
                return self._json(403, {"error": "Not allowed"})
            path = urlsplit(self.path).path
            if path == "/ping":
                return self._send(200, MARKER.encode(), "text/plain")
            if path == "/":
                html = (_WEB / "index.html").read_text(encoding="utf-8").replace("__VERSION__", __version__)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path.startswith("/static/") and (name := path[len("/static/"):]) in _STATIC:
                return self._send(200, (_WEB / name).read_bytes(), _STATIC[name])
            if path == "/events":
                return self._events()
            self._json(404, {"error": "Not found"})

        def _events(self) -> None:
            client: queue.Queue[str] = queue.Queue()
            with app.hass.lock:
                app.clients.append(client)
                client.put(app.snapshot())
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while not app.stop.is_set():
                    try:
                        text = client.get(timeout=15)
                        self.wfile.write(f"data: {text}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with app.hass.lock:
                    if client in app.clients:
                        app.clients.remove(client)
            self.close_connection = True

        def do_POST(self) -> None:  # noqa: N802
            if not self._local() or self.headers.get("X-Spool-Desktop") != "1":
                return self._json(403, {"error": "Not allowed"})
            if urlsplit(self.path).path != "/api":
                return self._json(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= 20_000_000:
                    return self._json(400, {"error": "The request is empty or too large."})
                msg = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(msg, dict) or not str(msg.get("type", "")).startswith("spool_inventory/"):
                    return self._json(400, {"error": "Bad request."})
                name = msg["type"].split("/", 1)[1]
                if name == "quit":
                    self._json(200, {"result": None})
                    app.stop.set()
                    return
                result = app.command(name, msg)
            except (api.ApiError, hastub.ServiceValidationError, ValueError, KeyError) as err:
                return self._json(400, {"error": api.readable(err)})
            except Exception as err:  # keep the app alive, say what happened
                _LOGGER.exception("Command failed")
                return self._json(500, {"error": f"Unexpected error: {err}"})
            self._json(200, {"result": result})

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # a second copy must not share the port (and the data)
