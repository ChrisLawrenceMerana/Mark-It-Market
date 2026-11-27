@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0multipdf_to_csv.py" "%~dp0Input" -o "%~dp0Output\2024_ncr_summary.csv" --no-strict
pause