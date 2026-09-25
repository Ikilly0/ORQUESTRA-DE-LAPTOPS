@echo off
REM Windows 10 launcher. Uses Python 3.12 (its Tkinter works inside a venv; 3.13.0 has a known Tcl bug).
cd /d "%~dp0"
echo [%date% %time%] start > run.log
set VENV=.venv312
if not exist %VENV%\Scripts\python.exe (
  echo creating venv >> run.log
  py -3.12 -m venv %VENV% >> run.log 2>&1
)
if not exist %VENV%\Scripts\python.exe (
  echo Python 3.12 not found, trying default python >> run.log
  py -3 -m venv %VENV% >> run.log 2>&1 || python -m venv %VENV% >> run.log 2>&1
)
echo installing packages >> run.log
%VENV%\Scripts\python -m pip install -q -r requirements.txt >> run.log 2>&1
echo launching app >> run.log
%VENV%\Scripts\python app.py >> run.log 2>&1
if errorlevel 1 (
  echo retry in safe audio mode >> run.log
  set KI_SAFE_AUDIO=1
  %VENV%\Scripts\python app.py >> run.log 2>&1
)
echo finished >> run.log
