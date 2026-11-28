@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0pdf_to_csv.py" %* -o "%~dp0Output"
pause
