@echo off
rem Starts Selvedge and opens it in your browser. Close this window to stop it.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8000"
echo Selvedge running at http://localhost:8000  (close this window to stop)
".venv\Scripts\python.exe" -m uvicorn techpack.app:app --port 8000
pause
