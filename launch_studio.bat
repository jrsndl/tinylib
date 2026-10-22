@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1

rem The browser configuration comes from tinylib.ini (config =), unless
rem TINYLIB_CONFIG is already set. The security file always comes from tinylib.ini.

rem With uv, first install whatever pyproject.toml asks for and .venv lacks (PySide6, numpy),
rem so a new dependency never stops the browser. --inexact keeps packages installed by other
rem syncs (dev tools, the build group). Up to date, this takes a moment and changes nothing.
rem A folder this account cannot write (a read-only share) keeps its existing environment.
where uv >nul 2>nul
if not errorlevel 1 (
    uv sync --extra pyside6 --inexact --quiet
    if errorlevel 1 (
        echo.
        echo uv could not install the Python packages; starting with what is installed.
        echo.
    )
)

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
    echo Standalone TinyLib requires Python 3.9 or newer and PySide2 or PySide6;
    echo install uv to have this launcher install them: https://docs.astral.sh/uv/
    pause
)
popd
exit /b %TINYLIB_EXIT_CODE%
