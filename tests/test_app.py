"""End-to-end tests of the local server (standard library only): python -m unittest discover -s tests"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spool_desktop import hastub  # noqa: E402
from spool_desktop.server import App, Server, make_handler  # noqa: E402


class Running:
    def __init__(self, folder: str) -> None:
        self.app = App(Path(folder))
        box = {"port": 0}
        self.server = Server(("127.0.0.1", 0), make_handler(self.app, lambda: box["port"]))
        box["port"] = self.server.server_address[1]
        self.port = box["port"]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, command: str, **fields):
        return self.raw({"type": f"spool_inventory/{command}", **fields})

    def raw(self, body: dict, headers: dict | None = None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Spool-Desktop": "1", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")

    def stop(self) -> None:
        self.app.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.app.close()


class AppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.r = Running(self.tmp.name)

    def tearDown(self) -> None:
        self.r.stop()
        self.tmp.cleanup()

    def add(self, **extra):
        status, body = self.r.call("add", vendor="Prusament", material="PETG", remaining_weight=900, **extra)
        self.assertEqual(status, 200, body)
        return body["result"]

    def test_pages_and_static_files(self) -> None:
        for path, needle in (("/", b"Spool Inventory"), ("/static/panel.js", b"spool-inventory-panel"),
                             ("/static/shim.js", b"callWS"), ("/static/catalog.json", b"vendors"), ("/ping", b"spool-inventory-desktop")):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.r.port}{path}", timeout=5) as res:
                self.assertIn(needle, res.read(), path)

    def test_add_update_weigh_consume(self) -> None:
        s = self.add(color_name="Galaxy Black", color="#222222", location="Drier")
        self.assertEqual(s["remaining_weight"], 900)
        status, body = self.r.call("update", spool_id=s["id"], notes="hello", price=99.5)
        self.assertEqual((status, body["result"]["notes"], body["result"]["price"]), (200, "hello", 99.5))
        status, body = self.r.call("consume", spool_id=s["id"], grams=100)
        self.assertEqual(body["result"]["remaining_weight"], 800)
        status, body = self.r.call("weigh", spool_id=s["id"], gross_weight=1000, spool_weight=250)
        self.assertEqual(body["result"]["remaining_weight"], 750)

    def test_archive_delete_restore_rebuy_history(self) -> None:
        s = self.add()
        self.assertTrue(self.r.call("archive", spool_id=s["id"])[1]["result"]["archived"])
        self.assertFalse(self.r.call("archive", spool_id=s["id"], archived=False)[1]["result"]["archived"])
        again = self.r.call("rebuy", spool_id=s["id"])[1]["result"]
        self.assertNotEqual(again["id"], s["id"])
        self.assertEqual(self.r.call("delete", spool_id=s["id"])[0], 200)
        self.assertEqual(self.r.call("restore", spool_id=s["id"])[1]["result"]["id"], s["id"])
        history = self.r.call("history", spool_id=s["id"])[1]["result"]
        self.assertTrue(len(history) >= 2)

    def test_settings_and_catalog(self) -> None:
        status, body = self.r.call("settings", low_threshold=200, currency="EUR", locations=["Shelf", "Box"])
        self.assertEqual(status, 200, body)
        self.assertEqual((body["result"]["currency"], body["result"]["locations"]), ("EUR", ["Shelf", "Box"]))
        entry = dict(vendor="MyBrand", material="PLA", name="Teal", hex="#008080", weights=[1000])
        self.assertEqual(self.r.call("catalog_add", **entry)[0], 200)
        events = urllib.request.urlopen(f"http://127.0.0.1:{self.r.port}/events", timeout=5)
        first = events.readline().decode()
        events.close()
        data = json.loads(first[len("data: "):])
        self.assertEqual(data["user_catalog"][0]["name"], "Teal")
        self.assertEqual(data["equipment"], {"printers": [], "tasks": [], "items": []})
        self.assertFalse(data["prusalink_found"])
        self.assertEqual(self.r.call("catalog_remove", vendor="MyBrand", material="PLA", name="Teal")[0], 200)

    def test_export_and_import(self) -> None:
        self.add(color_name="Red")
        backup = self.r.call("export")[1]["result"]
        self.assertEqual(self.r.call("import", data={"nonsense": 1}, replace=True)[0], 400)
        s2 = self.add(color_name="Blue")
        status, body = self.r.call("import", data=backup, replace=True)
        self.assertEqual(status, 200, body)
        ids = [s["id"] for s in self.r.call("export")[1]["result"]["spools"]]
        self.assertNotIn(s2["id"], ids)

    def test_readable_errors(self) -> None:
        status, body = self.r.call("update", spool_id="nope", notes="x")
        self.assertEqual(status, 400)
        self.assertIn("was not found", body["error"])
        self.assertEqual(self.r.call("add", vendor="X")[0], 400)  # material missing
        self.assertEqual(self.r.call("add", vendor="X", material="PLA", bogus=1)[0], 400)
        self.assertEqual(self.r.call("add", vendor="X", material="PLA", remaining_weight=-5)[0], 400)
        self.assertEqual(self.r.call("prusalink_start", name="x")[0], 400)  # PrusaLink needs Home Assistant
        self.assertEqual(self.r.raw({"type": "other/add"})[0], 400)

    def test_only_this_computer_and_our_page(self) -> None:
        body = {"type": "spool_inventory/add", "vendor": "A", "material": "PLA"}
        req = lambda headers: urllib.request.Request(f"http://127.0.0.1:{self.r.port}/api", data=json.dumps(body).encode(), method="POST", headers=headers)  # noqa: E731
        def code(headers):
            try:
                return urllib.request.urlopen(req(headers), timeout=5).status
            except urllib.error.HTTPError as err:
                return err.code
        ok = {"Content-Type": "application/json", "X-Spool-Desktop": "1"}
        self.assertEqual(code(ok), 200)
        self.assertEqual(code({"Content-Type": "application/json"}), 403)  # a web page can't set our header
        self.assertEqual(code({**ok, "Origin": "https://evil.example"}), 403)
        self.assertEqual(code({**ok, "Host": "evil.example"}), 403)  # DNS rebinding

    def test_data_survives_a_restart(self) -> None:
        s = self.add(color_name="Keep me")
        self.r.call("settings", currency="USD")
        self.r.stop()
        self.r = Running(self.tmp.name)
        ids = [x["id"] for x in self.r.call("export")[1]["result"]["spools"]]
        self.assertIn(s["id"], ids)
        self.assertEqual(self.r.call("settings")[1]["result"]["currency"], "USD")

    def test_files_are_written_whole(self) -> None:
        self.add()
        self.r.stop()
        files = list(Path(self.tmp.name).glob("*.json"))
        self.assertTrue(files)
        for f in files:
            json.loads(f.read_text(encoding="utf-8"))  # every file is complete, valid JSON
        self.assertFalse(list(Path(self.tmp.name).glob("*.tmp")))
        self.r = Running(self.tmp.name)

    def test_damaged_file_is_kept_not_overwritten(self) -> None:
        self.r.stop()
        main = next(Path(self.tmp.name).glob("*.spools.json"), None)
        if main is None:
            self.r = Running(self.tmp.name)
            self.add()
            self.r.stop()
            main = next(Path(self.tmp.name).glob("*.spools.json"))
        main.write_text("{ broken", encoding="utf-8")
        self.r = Running(self.tmp.name)
        self.assertTrue(main.with_suffix(".damaged").exists())

    def test_price_calculator(self) -> None:
        self.assertEqual(self.r.call("pricing_settings", energy_cost=1.2, labor_rate=40, failure_pct=5)[0], 200)
        status, body = self.r.call("machine_add", name="Core One+", price=5700, service=3000, life_hours=10000, kwh_per_h=0.24)
        self.assertEqual(status, 200, body)
        mid = body["result"]["id"]
        job = {"machine_id": mid, "hours": 8 + 31 / 60, "prep_min": 5, "post_min": 3, "filaments": [{"grams": 106, "price_per_kg": 170}]}
        status, body = self.r.call("price_calc", **job)
        self.assertEqual((status, body["result"]["filament"], body["result"]["suggested"]), (200, 18.02, 47.08))
        status, body = self.r.call("quote_save", customer="Test", description="Moss pole", quoted=50, **job)
        self.assertEqual(status, 200, body)
        qid = body["result"]["id"]
        self.assertEqual(self.r.call("quote_save", quote_id=qid, status="paid", **job)[1]["result"]["status"], "paid")
        for bad in ({"hours": -1}, {"filaments": "x"}, {"nope": 1}):
            self.assertNotEqual(self.r.call("price_calc", **bad)[0], 200)
        self.assertEqual(self.r.call("quote_delete", quote_id=qid)[0], 200)
        self.assertNotEqual(self.r.call("quote_delete", quote_id=qid)[0], 200)
        self.assertEqual(self.r.call("machine_delete", machine_id=mid)[0], 200)

    def test_printer_tab_commands(self) -> None:
        status, body = self.r.call("printer_add", name="Core One+", model="Prusa", starter=True)
        self.assertEqual(status, 200, body)
        pid = body["result"]["id"]
        state = json.loads(self.r.app.snapshot())["equipment"]
        self.assertEqual((len(state["printers"]), len(state["tasks"])), (1, 6))
        tid = state["tasks"][0]["id"]
        self.assertEqual(self.r.call("task_done", task_id=tid, note="ok")[0], 200)
        status, body = self.r.call("item_add", name="Nozzle 0.4", printer_id=pid, quantity=2, low_at=1, unit="pcs")
        self.assertEqual(status, 200, body)
        iid = body["result"]["id"]
        self.assertEqual(self.r.call("item_adjust", item_id=iid, delta=-1)[1]["result"]["quantity"], 1)
        self.assertTrue(json.loads(self.r.app.snapshot())["equipment"]["items"][0]["low"])
        self.assertNotEqual(self.r.call("item_add", name="")[0], 200)
        self.assertNotEqual(self.r.call("task_add", printer_id=pid, name="x", interval_days=0)[0], 200)
        self.assertEqual(self.r.call("printer_delete", printer_id=pid)[0], 200)
        self.assertEqual(json.loads(self.r.app.snapshot())["equipment"]["tasks"], [])

    def test_customer_list(self) -> None:
        self.assertEqual(self.r.call("customer_add", name="Alex")[1]["result"], ["Alex"])
        self.assertEqual(self.r.call("quote_save", customer="Sam")[0], 200)
        self.assertEqual(self.r.call("customer_rename", name="Sam", new_name="Samuel")[1]["result"], ["Alex", "Samuel"])
        self.assertNotEqual(self.r.call("customer_rename", name="Samuel", new_name="alex")[0], 200)
        self.assertNotEqual(self.r.call("customer_add", name="")[0], 200)
        self.assertEqual(self.r.call("customer_delete", name="Alex")[1]["result"], ["Samuel"])
        self.assertEqual(json.loads(self.r.app.snapshot())["pricing"]["customers"], ["Samuel"])

    def test_quit_stops_the_app(self) -> None:
        self.assertEqual(self.r.call("quit")[0], 200)
        self.assertTrue(self.r.app.stop.wait(2))


if __name__ == "__main__":
    unittest.main()
