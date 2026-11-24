"""The panel's commands, checked and passed on to the shared SpoolManager.

This mirrors the integration's websocket API (same command names, same fields, same limits), without Home Assistant
and without voluptuous. Printer / maintenance commands are not offered: the Windows app is a filament inventory only.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Callable

from .hastub import ServiceValidationError

_HERE = Path(__file__).parent
_MESSAGES: dict[str, str] = json.loads((_HERE / "core" / "messages.json").read_text(encoding="utf-8"))
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


class ApiError(Exception):
    """A problem with a command, shown to the user as is."""


# ---- value checks (same rules as the integration) -------------------------------------------------------------

def _str(value: Any, *, min_len: int = 0, max_len: int | None = None) -> str:
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise ApiError("A text value is expected.")
    text = str(value)
    if len(text) < min_len:
        raise ApiError("A required text is empty.")
    if max_len is not None and len(text) > max_len:
        raise ApiError(f"A text is longer than {max_len} characters.")
    return text


def _float(value: Any, low: float, high: float | None = None) -> float:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise ApiError("A number is expected.") from None
    if number != number or number < low or (high is not None and number > high):
        raise ApiError("A number is outside the allowed range.")
    return number


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).lower() in ("true", "on", "yes", "1"):
        return True
    if str(value).lower() in ("false", "off", "no", "0"):
        return False
    raise ApiError("A yes/no value is expected.")


def _color(value: Any) -> str:
    if isinstance(value, str) and (match := _HEX.match(value.strip())):
        return f"#{match.group(1).lower()}"
    raise ApiError("A color must look like #RRGGBB.")


_WEIGHT = lambda v: _float(v, 0, 50000)  # noqa: E731
_WEIGHT_POSITIVE = lambda v: _float(v, 1, 50000)  # noqa: E731
_TEXT = lambda v: _str(v)  # noqa: E731

_COMMON: dict[str, Callable[[Any], Any]] = {
    "product": _TEXT, "color_name": _TEXT, "color": _color,
    "diameter": lambda v: _float(v, 0.1, 5), "initial_weight": _WEIGHT_POSITIVE, "spool_weight": _WEIGHT,
    "price": lambda v: _float(v, 0), "location": _TEXT, "notes": _TEXT, "tag_id": _TEXT,
    "loaded": _bool, "reorder": _bool, "refill": _bool,
}
_ADD = {"vendor": lambda v: _str(v, min_len=1), "material": lambda v: _str(v, min_len=1),
        "remaining_weight": _WEIGHT, "gross_weight": _WEIGHT, **_COMMON}
_UPDATE = {"vendor": lambda v: _str(v, min_len=1), "material": lambda v: _str(v, min_len=1),
           "remaining_weight": _WEIGHT, **_COMMON}
_CATALOG_KEY = {
    "vendor": lambda v: _str(v, min_len=1, max_len=60), "material": lambda v: _str(v, min_len=1, max_len=30),
    "line": lambda v: _str(v, max_len=60), "name": lambda v: _str(v, min_len=1, max_len=80),
}


def _clean(msg: dict[str, Any], rules: dict[str, Callable[[Any], Any]], required: tuple[str, ...] = ()) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in msg.items():
        if key in ("id", "type"):
            continue
        if key not in rules:
            raise ApiError(f"Unknown field: {key}")
        out[key] = rules[key](value)
    for key in required:
        if key not in out:
            raise ApiError(f"Missing field: {key}")
    return out


# ---- the commands ---------------------------------------------------------------------------------------------

def payload(manager) -> dict[str, Any]:
    """What the panel shows (the same shape as the integration sends, with no printer data)."""
    return {
        "spools": [s.as_dict() for s in manager.spools],
        "summary": manager.summary(),
        "trash": manager.trash_summary(),
        "equipment": {"printers": [], "tasks": [], "items": []},
        "settings": manager.settings,
        "user_catalog": manager.user_catalog,
        "prusalink_found": False,
    }


def _spool_id(msg: dict[str, Any]) -> str:
    return _str(msg.get("spool_id"), min_len=1)


def handle(manager, run, command: str, msg: dict[str, Any]) -> Any:
    """Run one command. `run` executes a coroutine (for the few async manager calls)."""
    if command == "add":
        return manager.async_add(_clean(msg, _ADD, ("vendor", "material"))).as_dict()
    if command == "update":
        changes = _clean({k: v for k, v in msg.items() if k != "spool_id"}, _UPDATE)
        return manager.async_update(_spool_id(msg), changes).as_dict()
    if command == "weigh":
        data = _clean({k: v for k, v in msg.items() if k != "spool_id"}, {"gross_weight": _WEIGHT, "spool_weight": _WEIGHT}, ("gross_weight",))
        return manager.async_weigh(_spool_id(msg), data["gross_weight"], data.get("spool_weight")).as_dict()
    if command == "consume":
        data = _clean({k: v for k, v in msg.items() if k != "spool_id"}, {"grams": _WEIGHT}, ("grams",))
        return manager.async_consume(_spool_id(msg), data["grams"]).as_dict()
    if command == "delete":
        manager.async_delete(_spool_id(msg))
        return None
    if command == "archive":
        archived = _bool(msg["archived"]) if "archived" in msg else True
        return manager.async_archive(_spool_id(msg), archived).as_dict()
    if command == "restore":
        return manager.async_restore_deleted(_spool_id(msg)).as_dict()
    if command == "rebuy":
        return manager.async_rebuy(_spool_id(msg)).as_dict()
    if command == "history":
        spool = msg.get("spool_id")
        limit = int(_float(msg.get("limit", 200), 1, 2000))
        return manager.history(_str(spool) if spool else None, limit)
    if command == "export":
        return manager.export_data()
    if command == "import":
        if not isinstance(msg.get("data"), dict) or "replace" not in msg:
            raise ApiError("A backup file and the replace option are required.")
        return run(manager.async_import(msg["data"], _bool(msg["replace"])))
    if command == "settings":
        rules = {
            "low_threshold": lambda v: _float(v, 0, 5000), "notify": _bool,
            "currency": lambda v: _str(v, max_len=8),
            "locations": lambda v: _locations(v),
        }
        return manager.async_update_settings(_clean(msg, rules))
    if command == "catalog_add":
        rules = {**_CATALOG_KEY, "hex": _color,
                 "weights": lambda v: _weights(v), "tare": _WEIGHT}
        return manager.async_catalog_add(_clean(msg, rules, ("vendor", "material", "name")))
    if command == "catalog_remove":
        manager.async_catalog_remove(_clean(msg, _CATALOG_KEY, ("vendor", "material", "name")))
        return None
    raise ApiError(f"Unknown command: {command}")


def _locations(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 60:
        raise ApiError("The location list is invalid.")
    return [_str(v, max_len=60) for v in value]


def _weights(value: Any) -> list[float]:
    if not isinstance(value, list) or not 1 <= len(value) <= 6:
        raise ApiError("The weights list is invalid.")
    return [_WEIGHT_POSITIVE(v) for v in value]


def readable(err: Exception) -> str:
    """Text for the user from an error raised by the shared code."""
    if isinstance(err, ServiceValidationError):
        template = _MESSAGES.get(err.translation_key or "", "")
        if template:
            try:
                return template.format(**err.translation_placeholders)
            except (KeyError, IndexError):
                return template
        return err.translation_key or "Something went wrong."
    return str(err) or err.__class__.__name__
