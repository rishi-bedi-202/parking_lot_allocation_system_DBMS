#!/usr/bin/env bash
# Builds a single-file SmartParking executable for the OS you run this on (Linux/macOS).
set -e
cd "$(dirname "$0")"
python3 -m pip install --upgrade pyinstaller
python3 -m PyInstaller --onefile --name SmartParking --add-data "schema.sql:." --add-data "queries.sql:." parking.py
echo "Done. Your app is dist/SmartParking"
