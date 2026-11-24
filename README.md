<p align="center"><img src="spool_desktop/web/icon.png" width="96" alt=""></p>

# Spool Inventory for Windows

A small, portable filament inventory. No install, no account, no cloud, no Home Assistant needed: download one
file, double-click it, and your inventory opens in your browser.

It is the same inventory as [Spool Inventory for Home Assistant](https://github.com/andrei20092222-sudo/Spool-Inventory)
(same code, same panel), without anything about printers.

> **Status: early version (0.1).** It was just made and has not been used by many people yet. Please keep a backup
> of your data (Settings > Download backup).

## What it does

- **Inventory**: every spool with vendor, material, color, remaining grams, price, location and notes.
- **Catalog**: pick vendor, material and color from the Open Filament Database (about 150 brands, 14,700 colors),
  or add your own entries.
- **What fits?**, **weigh session**, **By color**, **By location**, **reorder list**, **archive**, **history with undo**.
- **Backups and CSV export.**

Not included: automatic subtraction, printer maintenance and parts. Those need Home Assistant and stay in the
[Home Assistant integration](https://github.com/andrei20092222-sudo/Spool-Inventory).

## How to use it

1. Download `SpoolInventory.exe` from the [latest release](../../releases/latest).
2. Put it in a folder of its own (for example `C:\Tools\SpoolInventory`) and double-click it.
3. A black window opens (leave it open) and your browser shows the inventory. To quit, press **Quit app** in the
   page, or close the black window.

Your data is stored in a `SpoolInventory_data` folder next to the program. To move or back up everything, copy that
folder. If the program sits in a folder you can't write to, the data goes to `%APPDATA%\SpoolInventory` instead.

**Windows may warn you** ("Windows protected your PC") because the program is not signed with a paid certificate.
You can check the file first: the release page shows its SHA-256 checksum. In PowerShell:
`Get-FileHash SpoolInventory.exe`.

### Options

| Option | What it does |
|---|---|
| `--data FOLDER` | keep the data in another folder |
| `--port NUMBER` | use another port (default 8765) |
| `--no-browser` | do not open the browser |

## Privacy and safety

- The program makes **no network calls** and sends nothing anywhere.
- It listens only on your own computer (127.0.0.1), and refuses requests from other websites or other computers.
- It has no installer and changes nothing outside its own folder.

## Moving between this app and Home Assistant

Both use the same backup format. In either one: Settings > Download backup, then in the other: Settings > Restore
from file. (Restoring "replace everything" overwrites what is there, so download a backup first.)

## Run it from the source

Needs Python 3.11 or newer, nothing else to install:

```
python -m spool_desktop
python -m unittest discover -s tests
```

The shared code is copied from the integration by `tools/sync_from_integration.py <path to the integration repo>`.

## How this was made

I built this together with [Claude](https://claude.ai), an AI assistant. I decided what it should do and tested the
idea; Claude wrote the code, reusing the code of the Home Assistant version. It has automatic tests and was checked
in a browser, but nobody else has reviewed the code yet. If you find a problem or want to review it, please open an
issue or a pull request.

## Disclaimer

Independent project, not affiliated with or endorsed by any filament maker or printer maker. All names are
trademarks of their owners. See `THIRD_PARTY_NOTICES.md`.

## License

MIT, see `LICENSE`.
