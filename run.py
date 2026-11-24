"""Entry point used to build the Windows program (PyInstaller needs a plain script)."""
from spool_desktop.main import main

raise SystemExit(main())
