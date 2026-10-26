"""Optional packages the window uses when they're installed."""

import tkinter as tk

# Windows 11 style light/dark theme. Without it: the built-in look.
try:
    import sv_ttk
    HAS_SV = True
except ImportError:  # pragma: no cover
    sv_ttk = None
    HAS_SV = False

# Drag and drop. Without it: the Add files button.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    BaseTk = TkinterDnD.Tk
    HAS_DND = True
except ImportError:  # pragma: no cover
    DND_FILES = None
    BaseTk = tk.Tk
    HAS_DND = False
