@echo off
REM Launch Chrome with the DevTools Protocol enabled on port 9222.
REM
REM This lets Desk_Deck list and switch tabs from the Apps overlay.
REM
REM Important: Chrome will refuse the debug-port flag if any other Chrome
REM window is open against the same user profile. If Chrome is already
REM running, close ALL Chrome windows first, then run this batch file.

set CHROME=
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe

if "%CHROME%"=="" (
  echo Could not find chrome.exe in the usual locations.
  echo Edit this batch file to set CHROME= to your Chrome install path.
  pause
  exit /b 1
)

echo Launching Chrome with --remote-debugging-port=9222
start "" "%CHROME%" --remote-debugging-port=9222 %*
