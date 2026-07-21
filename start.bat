@echo off
rem Launch the ticket tracker, then open it in the browser.
cd /d "%~dp0"
start "" http://localhost:5137
python app.py
