#!/bin/bash
# macOS launcher: double-click in Finder (or run ./run_macos.command in Terminal).
# The first run finds a Python 3.10+ with Tk 8.6+, creates .venv-macos and installs the packages
# (numpy, sounddevice, mido). Later runs start the app directly. Everything is logged in run.log.
cd "$(dirname "$0")" || exit 1
LOG="run.log"
VENV=".venv-macos"
echo "[$(date)] start (macOS)" > "$LOG"

say_error() {  # message in a macOS dialog (visible even when launched from the .app) and in the log
  echo "ERROR: $1" | tee -a "$LOG"
  osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"Keyboard Instruments\"" \
    >/dev/null 2>&1
}

good_python() {  # Python 3.10+ whose Tkinter is Tk 8.6 or newer (the macOS system python3 has Tk 8.5)
  "$1" - <<'PY' >/dev/null 2>&1
import sys, tkinter
sys.exit(0 if sys.version_info >= (3, 10) and tkinter.TkVersion >= 8.6 else 1)
PY
}

if [ ! -x "$VENV/bin/python" ]; then
  PY=""
  for c in python3.12 python3.13 python3.11 python3.10 \
           /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
           /opt/homebrew/bin/python3.1[0-9] /usr/local/bin/python3.1[0-9] python3; do
    p="$(command -v "$c" 2>/dev/null)"
    [ -n "$p" ] && good_python "$p" && { PY="$p"; break; }
  done
  if [ -z "$PY" ]; then
    say_error "No encontré Python 3.10+ con Tk 8.6. Instala Python 3.12 desde python.org (incluye Tk) y vuelve a abrir. También sirve: brew install python@3.12 python-tk@3.12"
    open "https://www.python.org/downloads/macos/" >/dev/null 2>&1
    exit 1
  fi
  echo "using $PY ($("$PY" --version 2>&1))" >> "$LOG"
  osascript -e 'display notification "Primera vez: instalando numpy, sounddevice y mido (1-2 minutos)…" with title "Keyboard Instruments"' >/dev/null 2>&1
  "$PY" -m venv "$VENV" >> "$LOG" 2>&1 || { say_error "No pude crear el entorno $VENV (mira run.log)."; exit 1; }
  "$VENV/bin/python" -m pip install -q --upgrade pip >> "$LOG" 2>&1
  "$VENV/bin/python" -m pip install -q -r requirements.txt >> "$LOG" 2>&1 \
    || { rm -rf "$VENV"; say_error "No pude instalar los paquetes (¿hay internet?). Mira run.log."; exit 1; }
fi

# macOS shows the accent popup (é, è, ê…) when a letter key is held, and then stops sending it:
# that would cut held notes and drum rolls. Turn it off for Python apps only (once).
if [ "$(defaults read org.python.python ApplePressAndHoldEnabled 2>/dev/null)" != "0" ]; then
  defaults write org.python.python ApplePressAndHoldEnabled -bool false
  echo "press-and-hold accent popup turned off for Python apps" >> "$LOG"
fi

echo "launching app" >> "$LOG"
exec "$VENV/bin/python" app.py >> "$LOG" 2>&1
