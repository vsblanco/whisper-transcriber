"""Entry point for the installed app (PyInstaller builds it into Whisper Transcriber.exe / .app)."""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(here))

from app import main  # noqa: E402

main()
