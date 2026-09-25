#!/usr/bin/env bash
# Linux (Mint / Ubuntu). First run creates a virtual environment and installs the dependencies.
# Needs once:  sudo apt install python3-venv python3-tk libportaudio2
cd "$(dirname "$0")"
VENV=.venv-linux
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || { echo "Falta: sudo apt install python3-venv python3-tk libportaudio2"; exit 1; }
  "$VENV/bin/pip" install -q -r requirements.txt || exit 1
fi
"$VENV/bin/python" -c "import tkinter" 2>/dev/null || { echo "Falta: sudo apt install python3-tk"; exit 1; }
exec "$VENV/bin/python" app.py
