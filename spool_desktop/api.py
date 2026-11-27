"""The panel's commands, checked and passed on to the shared SpoolManager.

This mirrors the integration's websocket API (same command names, same fields, same limits), without Home Assistant
and without voluptuous. The printer tab (maintenance, parts, consumables) is offered; PrusaLink and automatic subtraction are not, because they need Home Assistant.
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
        "equipment": manager.equipment.payload(),
        "pricing": manager.pricing.payload(),
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
    if command in _EQUIPMENT:
        return _equipment(manager.equipment, command, {k: v for k, v in msg.items() if k not in ("id", "type")})
    if command in _PRICING:
        return _pricing(manager, command, msg)
    raise ApiError(f"Unknown command: {command}")


# ---- price calculator (same fields and limits as the integration) ----------------------------------------------

_MONEY = lambda v: _float(v, 0, 1_000_000)  # noqa: E731
_JOB = {
    "machine_id": lambda v: _str(v, max_len=40), "hours": lambda v: _float(v, 0, 10000),
    "filaments": lambda v: _filaments(v),
    **{k: _MONEY for k in ("prep_min", "post_min", "consumables", "extra", "design", "quoted", "shipping")},
}
_RATES = {k: _MONEY for k in ("energy_cost", "labor_rate", "failure_pct", "markup_pct", "markup_small_pct", "markup_large_pct", "large_threshold")}
_MACHINE = {"name": lambda v: _str(v, min_len=1, max_len=60), "price": _MONEY, "service": _MONEY,
            "life_hours": lambda v: _float(v, 0, 1_000_000), "kwh_per_h": lambda v: _float(v, 0, 100)}
_PRICING = {"price_calc", "pricing_settings", "machine_add", "machine_update", "machine_delete", "quote_save", "quote_delete", "customer_add", "customer_rename", "customer_delete"}


# ---- printers, maintenance tasks, parts and consumables (same fields and limits as the integration) -------------

_INTERVAL = lambda v: int(_float(v, 1, 3650))  # noqa: E731
_QTY = lambda v: _float(v, 0, 100000)  # noqa: E731
_ITEM = {"printer_id": lambda v: _str(v, max_len=40), "category": lambda v: _str(v, max_len=40), "name": lambda v: _str(v, min_len=1, max_len=80),
         "quantity": _QTY, "unit": lambda v: _str(v, max_len=12), "low_at": _QTY, "notes": lambda v: _str(v, max_len=300), "reorder": lambda v: _bool(v)}
_EQUIPMENT = {"printer_add", "printer_update", "printer_delete", "task_add", "task_update", "task_done", "task_delete",
              "item_add", "item_update", "item_adjust", "item_delete"}


def _equipment(eq, command: str, msg: dict[str, Any]) -> Any:
    ident = lambda key: _str(msg.get(key), min_len=1)  # noqa: E731
    rest = lambda key: {k: v for k, v in msg.items() if k != key}  # noqa: E731
    if command == "printer_add":
        d = _clean(msg, {"name": lambda v: _str(v, min_len=1, max_len=60), "model": lambda v: _str(v, max_len=60), "starter": _bool}, ("name",))
        return eq.printer_add(d["name"], d.get("model", ""), d.get("starter", False))
    if command == "printer_update":
        return eq.printer_update(ident("printer_id"), _clean(rest("printer_id"), {"name": _TEXT, "model": _TEXT, "notes": _TEXT}))
    if command == "printer_delete":
        return eq.printer_delete(ident("printer_id"))
    if command == "task_add":
        d = _clean(rest("printer_id"), {"name": lambda v: _str(v, min_len=1, max_len=80), "interval_days": _INTERVAL, "last_done": _TEXT, "notes": _TEXT}, ("name", "interval_days"))
        return eq.task_add(ident("printer_id"), d["name"], d["interval_days"], d.get("last_done"), d.get("notes", ""))
    if command == "task_update":
        return eq.task_update(ident("task_id"), _clean(rest("task_id"), {"name": _TEXT, "interval_days": _INTERVAL, "last_done": _TEXT, "notes": _TEXT}))
    if command == "task_done":
        d = _clean(rest("task_id"), {"date": _TEXT, "note": lambda v: _str(v, max_len=200)})
        return eq.task_done(ident("task_id"), d.get("date"), d.get("note", ""))
    if command == "task_delete":
        return eq.task_delete(ident("task_id"))
    if command == "item_add":
        return eq.item_add(_clean(msg, _ITEM, ("name",)))
    if command == "item_update":
        return eq.item_update(ident("item_id"), _clean(rest("item_id"), _ITEM))
    if command == "item_adjust":
        return eq.item_adjust(ident("item_id"), _float(msg.get("delta"), -100000, 100000))
    return eq.item_delete(ident("item_id"))


def _filaments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 4:
        raise ApiError("The filament list is invalid.")
    out = []
    for f in value:
        if not isinstance(f, dict):
            raise ApiError("A filament entry is invalid.")
        out.append({"spool_id": _str(f.get("spool_id", ""), max_len=40), "name": _str(f.get("name", ""), max_len=120),
                    "grams": _float(f.get("grams"), 0, 100000), "price_per_kg": _MONEY(f.get("price_per_kg"))})
    return out


def _pricing(manager, command: str, msg: dict[str, Any]) -> Any:
    pr = manager.pricing
    rest = {k: v for k, v in msg.items() if k not in ("id", "type")}
    if command == "price_calc":
        return pr.calculate(_clean(rest, _JOB))
    if command == "pricing_settings":
        return pr.update_settings(_clean(rest, _RATES))
    if command == "machine_add":
        return pr.machine_add(_clean(rest, _MACHINE, ("name",)))
    if command == "machine_update":
        return pr.machine_update(_str(rest.pop("machine_id", None), min_len=1), _clean(rest, _MACHINE))
    if command == "machine_delete":
        return pr.machine_delete(_str(rest.get("machine_id"), min_len=1))
    if command == "quote_delete":
        return pr.quote_delete(_str(rest.get("quote_id"), min_len=1))
    if command == "customer_add":
        return pr.customer_add(_str(rest.get("name"), min_len=1, max_len=80))
    if command == "customer_rename":
        return pr.customer_rename(_str(rest.get("name"), min_len=1, max_len=80), _str(rest.get("new_name"), min_len=1, max_len=80))
    if command == "customer_delete":
        return pr.customer_delete(_str(rest.get("name"), min_len=1, max_len=80))
    rules = {**_JOB, "customer": lambda v: _str(v, max_len=80), "description": lambda v: _str(v, max_len=200),
             "status": lambda v: v if v in ("draft", "sent", "paid") else _bad(), "quote_id": lambda v: _str(v, min_len=1)}
    data = _clean(rest, rules)
    return pr.quote_save({k: v for k, v in data.items() if k != "quote_id"}, data.get("quote_id"))


def _bad():
    raise ApiError("Unknown status.")


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
