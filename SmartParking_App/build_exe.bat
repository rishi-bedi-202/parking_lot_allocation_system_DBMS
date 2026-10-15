@echo off
rem Builds a single-file SmartParking.exe (run this on Windows; PyInstaller cannot cross-compile).
cd /d "%~dp0"
set PY=py
where py >nul 2>nul || set PY=python
%PY% -m pip install --upgrade pyinstaller
%PY% -m PyInstaller --onefile --name SmartParking --add-data "schema.sql;." --add-data "queries.sql;." parking.py
echo.
echo Done. Your app is dist\SmartParking.exe  (double-click it; parking.db is created beside it)
pause
