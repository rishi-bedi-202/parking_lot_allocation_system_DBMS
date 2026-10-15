@echo off
cd /d "%~dp0"
set PY=py
where py >nul 2>nul || set PY=python
%PY% parking.py %*
if errorlevel 1 echo. & echo Could not run. Install Python 3 from https://www.python.org/downloads/ and tick "Add to PATH".
pause
