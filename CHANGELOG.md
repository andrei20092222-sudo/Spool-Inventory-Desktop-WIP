# Changelog

## 0.2.0 - Price calculator

- New **Price** tab (same as in the Home Assistant version 1.1.0): what a print costs and what to ask for it. Filament
  cost comes from your own spools, plus electricity, machine wear, your time, extras and a failure allowance. Suggested
  price, fixed-markup price, profit, saved quotes with customer and status.
- **Printer tab** is now part of the app: maintenance tasks with intervals and overdue marks, parts and consumables with low-stock warnings. Only PrusaLink and automatic subtraction stay in Home Assistant.
- New layout like the Home Assistant version: *Filament*, *Printer* and *Price* are buttons in the blue bar.
- The spool buttons (loaded, reorder, weigh, use filament, ⋯ menu) now also show in *By location* and under each color in *By color*.
- Customer list for quotes (add, rename, remove; new names are added when a quote is saved).
- Machines (price, service costs, working life, power use) and rates are set inside the app; they start at zero.
- Included in backups, so quotes and machines move between this app and Home Assistant too.

## 0.1.0 - First version

- Standalone Spool Inventory for Windows: one program, no install, no Home Assistant needed.
- Same inventory as the Home Assistant integration (same code and panel): spools, catalog of about 150 brands, weigh
  session, What fits?, By color, By location, archive, history with undo, reorder list, backups, CSV export.
- Your data is kept in a folder next to the program. A backup made in the Home Assistant version can be restored here,
  and the other way around.
- Left out on purpose: everything about printers (automatic subtraction, maintenance, parts). That stays in Home
  Assistant.
