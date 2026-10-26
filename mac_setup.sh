#!/bin/bash
# Sets up the Python version of Whisper Transcriber on a Mac (the one with every feature,
# including speaker detection). In Terminal:   bash mac_setup.sh
# Afterwards, double-click "Whisper Transcriber.command" to open the app.
set -e
cd "$(dirname "$0")"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is needed first. Install it from https://brew.sh, then run this again."
  exit 1
fi

echo "Installing Python 3.12, Tk and ffmpeg (Homebrew)..."
brew install python@3.12 python-tk@3.12 ffmpeg
echo "Installing VLC (for the video preview)..."
brew install --cask vlc || echo "VLC may already be installed; skipping."

PY="$(brew --prefix python@3.12)/bin/python3.12"
echo "Creating the app's own Python environment in .venv ..."
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt faster-whisper

cat > "Whisper Transcriber.command" <<'LAUNCH'
#!/bin/bash
cd "$(dirname "$0")"
exec .venv/bin/python Transcriber.pyw "$@"
LAUNCH
chmod +x "Whisper Transcriber.command"

echo
echo "Done. Double-click \"Whisper Transcriber.command\" to open the app."
echo "Optional: speaker detection:   .venv/bin/python -m pip install pyannote.audio"
