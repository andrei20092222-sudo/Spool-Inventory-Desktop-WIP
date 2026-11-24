"""Spool Inventory for Windows (and anywhere Python runs): a standalone inventory with the same data and panel."""

from pathlib import Path

__version__ = (Path(__file__).parent / "VERSION").read_text(encoding="utf-8").strip()
