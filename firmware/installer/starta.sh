#!/bin/sh
# Startar webbinstalleraren på http://localhost:8765 (Linux, macOS och WSL).
# Kör i den uppackade mappen:  sh starta.sh
# Stäng med Ctrl+C.
cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
  python=python3
elif command -v python >/dev/null 2>&1; then
  python=python
else
  echo "Python saknas. Installera det, till exempel: sudo apt install python3"
  exit 1
fi

# I WSL finns webbläsaren och USB-porten i Windows. Öppna sidan där, men
# bara om servern faktiskt startade.
if grep -qi microsoft /proc/version 2>/dev/null && command -v cmd.exe >/dev/null 2>&1; then
  (
    sleep 1
    if command -v curl >/dev/null 2>&1 && ! curl -sf -o /dev/null http://localhost:8765/; then
      exit 0
    fi
    cmd.exe /c start "" msedge http://localhost:8765/ >/dev/null 2>&1
  ) &
  exec "$python" starta.py --no-browser
fi

exec "$python" starta.py
