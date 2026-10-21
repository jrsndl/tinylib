@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1

rem The browser configuration comes from tinylib.ini (config =), unless
rem TINYLIB_CONFIG is already set. The security file always comes from tinylib.ini.

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0launch.py"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        py -3 "%~dp0launch.py"
    ) else (
        python "%~dp0launch.py"
    )
)

set "TINYLIB_EXIT_CODE=%ERRORLEVEL%"
if not "%TINYLIB_EXIT_CODE%"=="0" (
    echo.
    echo TinyLib could not start. Check the error above.
    echo Standalone TinyLib requires Python 3.9 or newer and PySide2 or PySide6.
    pause
)
popd
exit /b %TINYLIB_EXIT_CODE%
