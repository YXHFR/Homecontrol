@echo off
rem Startar webbinstalleraren pa http://localhost:8765 (Windows).
rem Dubbelklicka pa filen i den uppackade mappen. Stang fonstret nar du ar klar.
rem (De har raderna ar utan a, a och o eftersom de lases innan chcp 65001.)
chcp 65001 >nul
cd /d "%~dp0"

if not exist starta.py (
  echo.
  echo Packa upp hela zip-filen först: högerklicka på den och välj Extrahera alla.
  echo Dubbelklicka sedan på starta.bat i den uppackade mappen.
  echo.
  goto slut
)

py -3 --version >nul 2>nul
if %errorlevel%==0 (
  py -3 starta.py
  goto slut
)
python --version >nul 2>nul
if %errorlevel%==0 (
  python starta.py
  goto slut
)

echo.
echo Python saknas på datorn. Installera det så här:
echo   1. Öppna PowerShell (tryck på Start, skriv PowerShell och tryck Enter).
echo   2. Skriv:  winget install Python.Python.3.13
echo   3. Stäng PowerShell och dubbelklicka på starta.bat igen.
echo.

:slut
pause
