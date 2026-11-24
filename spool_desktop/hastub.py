"""A very small stand-in for the parts of Home Assistant that the shared inventory code uses.

The shared files (core/manager.py, core/equipment.py, core/const.py) come straight from the integration and import
a handful of names from `homeassistant`. This module provides just those names, backed by plain files and plain
Python, so the same code runs without Home Assistant. Install it with install() before importing the core.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import threading
from types import ModuleType
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)


# ---- the "hass" object --------------------------------------------------------------------------------------------

class _Bus:
    def __init__(self, hass: "Hass") -> None:
        self._hass = hass

    def async_fire(self, event: str, data: dict[str, Any] | None = None) -> None:
        self._hass.events.append((event, data or {}))
        del self._hass.events[:-100]


class Hass:
    """Minimal hass: a data folder, an event list, notifications and in-process signals."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, Any] = {}
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.notifications: dict[str, dict[str, str]] = {}
        self.bus = _Bus(self)
        self.lock = threading.RLock()
        self._listeners: dict[str, list[Callable[..., None]]] = {}
        self._timers: dict[str, threading.Timer] = {}


# ---- storage ---------------------------------------------------------------------------------------------------

class Store:
    """JSON file per key with a version, like Home Assistant's .storage files."""

    def __init__(self, hass: Hass, version: int, key: str, *, minor_version: int = 1) -> None:
        self.hass = hass
        self.version = version
        self.key = key
        self.minor_version = minor_version

    def __class_getitem__(cls, _item):  # lets the shared code write Store[dict[str, Any]]
        return cls

    @property
    def path(self) -> Path:
        return self.hass.folder / f"{self.key}.json"

    async def async_load(self) -> Any:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            # an unreadable file is kept aside instead of being overwritten
            try:
                self.path.replace(self.path.with_suffix(".damaged"))
            except OSError:
                pass
            _LOGGER.error("Could not read %s, it was kept as .damaged", self.path)
            return None
        data = raw.get("data")
        old = int(raw.get("version", 1))
        if old < self.version and hasattr(self, "_async_migrate_func"):
            data = await self._async_migrate_func(old, int(raw.get("minor_version", 1)), data)
            await self.async_save(data)
        return data

    async def async_save(self, data: Any) -> None:
        with self.hass.lock:
            self._write(data)

    def _write(self, data: Any) -> None:
        payload = {"version": self.version, "minor_version": self.minor_version, "key": self.key, "data": data}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)  # a crash never leaves a half-written file

    def async_delay_save(self, data_func: Callable[[], Any], delay: float = 0) -> None:
        """Write shortly after the last change (changes in between are merged into one write)."""
        timers = self.hass._timers
        old = timers.pop(self.key, None)
        if old:
            old.cancel()

        def run() -> None:
            with self.hass.lock:
                timers.pop(self.key, None)
                self._write(data_func())

        timer = threading.Timer(delay, run)
        timer.daemon = False  # the final save is not lost when the program closes right after
        timers[self.key] = timer
        timer.start()


def flush_pending(hass: Hass) -> None:
    """Run delayed saves now (used when the program closes)."""
    for timer in list(hass._timers.values()):
        timer.cancel()
        timer.function()  # type: ignore[attr-defined]


# ---- the module tree that the shared code imports from ----------------------------------------------------------

class ServiceValidationError(Exception):
    """An error with a message key; the server turns it into readable text."""

    def __init__(self, *args: Any, translation_domain: str | None = None, translation_key: str | None = None,
                 translation_placeholders: dict[str, str] | None = None) -> None:
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders or {}


def _module(name: str, **attrs: Any) -> ModuleType:
    mod = ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


def install() -> None:
    """Register the stand-in modules (does nothing when real Home Assistant is importable)."""
    if "homeassistant" in sys.modules and getattr(sys.modules["homeassistant"], "_spool_stub", False):
        return

    class HomeAssistant(Hass):  # type annotation target
        pass

    def callback(func):
        return func

    def async_dispatcher_connect(hass: Hass, signal: str, target: Callable[..., None]) -> Callable[[], None]:
        hass._listeners.setdefault(signal, []).append(target)

        def remove() -> None:
            try:
                hass._listeners.get(signal, []).remove(target)
            except ValueError:
                pass

        return remove

    def async_dispatcher_send(hass: Hass, signal: str, *args: Any) -> None:
        for target in list(hass._listeners.get(signal, [])):
            try:
                target(*args)
            except Exception:  # a broken listener must not break a save
                _LOGGER.exception("Listener for %s failed", signal)

    def pn_create(hass: Hass, message: str, title: str | None = None, notification_id: str | None = None) -> None:
        hass.notifications[notification_id or message] = {"message": message, "title": title or ""}

    def pn_dismiss(hass: Hass, notification_id: str) -> None:
        hass.notifications.pop(notification_id, None)

    def parse_datetime(value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    root = _module("homeassistant", _spool_stub=True)
    comps = _module("homeassistant.components")
    pn = _module("homeassistant.components.persistent_notification", async_create=pn_create, async_dismiss=pn_dismiss)
    comps.persistent_notification = pn
    _module("homeassistant.core", HomeAssistant=HomeAssistant, callback=callback)
    _module("homeassistant.exceptions", ServiceValidationError=ServiceValidationError)
    helpers = _module("homeassistant.helpers")
    disp = _module("homeassistant.helpers.dispatcher", async_dispatcher_send=async_dispatcher_send,
                   async_dispatcher_connect=async_dispatcher_connect)
    stor = _module("homeassistant.helpers.storage", Store=Store)
    helpers.dispatcher, helpers.storage = disp, stor
    util = _module("homeassistant.util")
    dt = _module("homeassistant.util.dt", now=lambda: datetime.now().astimezone(),
                 utcnow=lambda: datetime.now(timezone.utc), parse_datetime=parse_datetime)
    util.dt = dt
    root.components, root.core, root.helpers, root.util = comps, sys.modules["homeassistant.core"], helpers, util


def run(coro):
    """Run a coroutine from normal code (the stand-in coroutines never really wait)."""
    return asyncio.run(coro)
