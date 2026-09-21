@echo off
rem DiscRipper launcher. Prefers the bundled private Python (created by
rem "rip.cmd bundle") so the folder runs on machines with no Python installed.
rem
rem Exception: the GUI needs tkinter, and the "embeddable" Python distribution
rem deliberately ships without it - so "rip.cmd gui" looks for a full Python
rem first and only falls back to the bundled one (which then explains itself).
if /I "%~1"=="gui" goto :wanttk
if exist "%~dp0python\python.exe" goto :embedded
where py >nul 2>nul
if %errorlevel%==0 goto :pylauncher
where python >nul 2>nul
if %errorlevel%==0 goto :pythonexe
echo Python 3.11+ is required. Either:
echo   - install it:            winget install Python.Python.3.12
echo   - or on another machine, run "rip.cmd bundle" first to embed a
echo     private Python inside this folder, then copy the folder over.
exit /b 2

:wanttk
where py >nul 2>nul
if %errorlevel%==0 goto :pylauncher
where python >nul 2>nul
if %errorlevel%==0 goto :pythonexe
if exist "%~dp0python\python.exe" goto :embedded
echo Python 3.11+ with tkinter is required for the GUI.
echo   install it:  winget install Python.Python.3.12
echo The text wizard exposes every option too:  rip.cmd
exit /b 2

:embedded
"%~dp0python\python.exe" "%~dp0discripper.py" %*
exit /b %errorlevel%

:pylauncher
py -3 "%~dp0discripper.py" %*
exit /b %errorlevel%

:pythonexe
python "%~dp0discripper.py" %*
exit /b %errorlevel%
