#!/usr/bin/env bash
# Linux (Mint / Ubuntu). First run creates a virtual environment and installs the dependencies.
# Needs once:  sudo apt install python3-venv python3-tk libportaudio2
cd "$(dirname "$0")"
VENV=.venv-linux
FALTA="Falta: sudo apt install python3-venv python3-tk libportaudio2"
if [ ! -x "$VENV/bin/pip" ]; then
  rm -rf "$VENV"
  python3 -m venv "$VENV" || { rm -rf "$VENV"; echo "$FALTA"; exit 1; }
fi
if ! "$VENV/bin/python" -c "import numpy, sounddevice, mido" 2>/dev/null; then
  echo "Instalando dependencias (solo la primera vez)..."
  "$VENV/bin/pip" install -q -r requirements.txt || exit 1
fi
"$VENV/bin/python" -c "import tkinter" 2>/dev/null || { echo "Falta: sudo apt install python3-tk"; exit 1; }
"$VENV/bin/python" -c "import sounddevice" 2>/dev/null || { echo "Falta: sudo apt install libportaudio2"; exit 1; }

# Protecciones para que la app nunca pueda congelar el PC:
#  - maximo 2 GB de RAM (si algo se descontrola, Linux cierra solo la app, no el sistema)
#  - prioridad baja de CPU (nice) para que el escritorio siga respondiendo
#  - todo lo que imprime queda en run.log (con hora) para diagnosticar despues de un reinicio
CMD=(nice -n 5 "$VENV/bin/python" -u app.py)
if command -v systemd-run >/dev/null && systemd-run --user --scope -q -p MemoryMax=2G true 2>/dev/null; then
  CMD=(systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 "${CMD[@]}")
fi
echo "=== $(date '+%F %T') inicio (kernel $(uname -r)) ===" > run.log
"${CMD[@]}" 2>&1 | while IFS= read -r l; do printf '%s %s\n' "$(date +%T)" "$l"; done | tee -a run.log
