@echo off
REM Batch script to run model.py with the correct Python environment
REM This ensures all dependencies are available

cd /d "%~dp0"

REM Activate virtual environment and run the script
call .b..\venv\Scripts\activateat
python -u model.py
pause
