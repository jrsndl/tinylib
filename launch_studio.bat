@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1

rem TinyLib automatically prefers config\studio.local.json when it exists.
set "TINYLIB_CONFIG=%~dp0config\studio.json"

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0launch.py" --config "%TINYLIB_CONFIG%"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        py -3 "%~dp0launch.py" --config "%TINYLIB_CONFIG%"
    ) else (
        python "%~dp0launch.py" --config "%TINYLIB_CONFIG%"
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
