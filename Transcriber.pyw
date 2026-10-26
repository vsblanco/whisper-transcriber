#! python3.12
# The line above tells the Windows Python launcher which Python to use (the one with Whisper installed).
# Change 3.12 if your packages live in a different Python version.
"""Double-click to open Whisper Transcriber without a console window.

.pyw files run with pythonw, so no black window flashes on screen.
Files dropped onto this file are added to the queue.
"""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
os.chdir(here)

from app import main  # noqa: E402

main()
