"""Double-click to start AL80 Screen with no console window (Windows)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

try:
    from al80.gui import run
    run(dry_run="--dry-run" in sys.argv, hidden="--hidden" in sys.argv)
except ImportError as exc:
    import tkinter
    from tkinter import messagebox
    tkinter.Tk().withdraw()
    messagebox.showerror("AL80 Screen", f"A package is missing: {exc.name}\n\n"
                         "Open a terminal in this folder and run:\npip install -r requirements.txt")
