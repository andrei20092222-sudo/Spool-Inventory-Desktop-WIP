"""Copy the shared inventory code from the Home Assistant integration repo.

Usage: python tools/sync_from_integration.py ../Spool-Inventory

The Windows app runs the integration's own manager.py, equipment.py, pricing.py and const.py (so the data and the rules are
identical) and the same panel.js. Small patches are applied to the panel: Home Assistant wording becomes app wording.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# (old text, new text): the Home Assistant wording becomes app wording.
PANEL_PATCHES = [
    ('h("label", { class: "row2" }, notify, h("span", {}, "Show a Home Assistant notification when a spool gets low or empty")),\n'
     '      h("p", { class: "hint" }, "Home Assistant also fires the events spool_inventory_low and spool_inventory_empty, '
     'so an automation can send a phone message."),\n', ""),
    ("A Home Assistant notification and the shopping list use this.", "The low marker and the reorder list use this."),
    ("Home Assistant also keeps an automatic daily copy inside its .storage folder.",
     "The app also keeps an automatic daily copy in its data folder."),
]

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "spool_desktop"


def main(src_repo: str) -> None:
    src = Path(src_repo) / "custom_components" / "spool_inventory"
    for name in ("manager.py", "equipment.py", "pricing.py", "const.py"):
        shutil.copyfile(src / name, PKG / "core" / name)
    panel = (src / "www" / "panel.js").read_text(encoding="utf-8")
    for old, new in PANEL_PATCHES:
        if old not in panel:
            raise SystemExit(f"panel.js changed, update this patch: {old[:60]!r}")
        panel = panel.replace(old, new)
    (PKG / "web" / "panel.js").write_text(panel, encoding="utf-8")
    catalog = json.loads((src / "www" / "catalog.json").read_text(encoding="utf-8"))
    for vendor in catalog.get("vendors", []):
        vendor["logo"] = None  # no brand logos are shipped: the panel shows the two-letter code instead
    (PKG / "web" / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    en = json.loads((src / "translations" / "en.json").read_text(encoding="utf-8"))
    messages = {k: v["message"] for k, v in en.get("exceptions", {}).items()}
    (PKG / "core" / "messages.json").write_text(json.dumps(messages, indent=1), encoding="utf-8")
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    (PKG / "core" / "SOURCE_VERSION").write_text(manifest["version"] + "\n", encoding="utf-8")
    print("synced from integration", manifest["version"])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../Spool-Inventory")
