@echo off
cd /d "%~dp0"
python download_pdfs.py --convert
echo.
pause