@echo off
REM Registers Desk_Deck to start at login via Windows Task Scheduler.
REM Run this once. Use uninstall_autostart.bat to remove.

setlocal
set TASK_NAME=Desk_Deck
set SCRIPT_DIR=%~dp0
set RUN_CMD="%SCRIPT_DIR%run.bat"

schtasks /Query /TN %TASK_NAME% >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Task "%TASK_NAME%" already exists.
  echo To replace, run uninstall_autostart.bat first.
  exit /b 1
)

schtasks /Create /TN %TASK_NAME% /SC ONLOGON /RL LIMITED ^
  /TR "cmd /c \"cd /d %SCRIPT_DIR% && start \"\" %RUN_CMD%\"" /F

if %ERRORLEVEL%==0 (
  echo Installed. Desk_Deck will start at your next login.
) else (
  echo Failed.
)
endlocal
