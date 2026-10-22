@echo off
rem Freezes the standalone TinyLib browser into dist\TinyLib\, using uv and PyInstaller.
rem No Python is needed where TinyLib.exe runs.
rem
rem   build.bat            windowed TinyLib.exe
rem   build.bat console    TinyLib.exe with a console window, to read startup errors
rem
rem Result (dist\TinyLib\):
rem   TinyLib.exe, _internal\        the browser; keep _internal\ next to the exe
rem   config\, actions\              the sample configuration and the shipped actions
rem   tinylib.ini.example, README.md, docs\
rem TinyLib.exe reads tinylib.ini, config\ and actions\ from its own folder, like launch.py
rem does from the repository. Copy tinylib.ini.example to tinylib.ini there and set it up.
rem The farm worker is not part of the exe; Deadline runs worker.py with its own Python.

setlocal
cd /d "%~dp0"

set "OUT=dist\TinyLib"
set "WORK=build\pyinstaller"
set "MODE=--windowed"
if /i "%~1"=="console" set "MODE=--console"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is not installed or not on PATH: https://docs.astral.sh/uv/getting-started/installation/
    goto :fail
)

rem --- environment: PySide6 and PyInstaller from pyproject.toml / uv.lock ------------------------
echo === uv sync --extra pyside6 --group build
uv sync --extra pyside6 --group build
if errorlevel 1 goto :fail
uv run python -c "import PySide6, PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo PySide6 or PyInstaller did not install. Run: uv sync --extra pyside6 --group build
    goto :fail
)
for /f "delims=" %%V in ('uv run python -c "import tinylib; print(tinylib.__version__)"') do set "VERSION=%%V"
rem The DLLs folder of the Python uv installed. Its OpenSSL (libssl/libcrypto) must be the one in the
rem exe: _ssl.pyd fails with "DLL load failed ... procedure could not be found" next to any other
rem build of OpenSSL, and other programs often put theirs on PATH, where PyInstaller would find it.
for /f "delims=" %%D in ('uv run python -c "import sys; print(sys.base_prefix)"') do set "PYDLLS=%%D\DLLs"
if not exist "%PYDLLS%\_ssl.pyd" (
    echo Cannot find the Python DLLs folder: %PYDLLS%
    goto :fail
)
set "PATH=%PYDLLS%;%PATH%"

rem --- clean ------------------------------------------------------------------------------------
if exist "%OUT%" rmdir /s /q "%OUT%"
if exist "%OUT%" (
    echo Cannot remove %OUT% - is TinyLib.exe still running?
    goto :fail
)
if exist "build\dist" rmdir /s /q "build\dist"

rem --- freeze: one-folder exe, which starts faster than one-file with Qt inside -----------------
rem All tinylib modules are collected: actions import them by name at run time.
rem The stylesheet and icons are read from the package folder, so they go in as data.
rem PySide2 and Nuke are left out; the exe always uses PySide6.
echo.
echo === building TinyLib.exe %VERSION%
uv run python -m PyInstaller --noconfirm --clean --onedir %MODE% --noupx --name TinyLib ^
    --distpath build\dist --workpath "%WORK%" --specpath build ^
    --paths "%CD%" --collect-submodules tinylib ^
    --add-data "%CD%\tinylib\standalone.qss;tinylib" ^
    --add-data "%CD%\tinylib\icons;tinylib\icons" ^
    --exclude-module PySide2 --exclude-module shiboken2 --exclude-module nuke ^
    "%CD%\launch.py"
if errorlevel 1 goto :fail
if not exist "build\dist\TinyLib\TinyLib.exe" goto :fail

rem --- OpenSSL: always the Python's own, whatever PyInstaller found ---------------------------
for %%F in ("%PYDLLS%\libssl*.dll" "%PYDLLS%\libcrypto*.dll") do (
    copy /y "%%~fF" "build\dist\TinyLib\_internal\" >nul
    if errorlevel 1 goto :fail
    fc /b "%%~fF" "build\dist\TinyLib\_internal\%%~nxF" >nul
    if errorlevel 1 (
        echo %%~nxF in the build does not match %%~fF
        goto :fail
    )
)

rem --- assemble ---------------------------------------------------------------------------------
echo.
echo === assembling %OUT%
xcopy /e /i /q /y "build\dist\TinyLib" "%OUT%" >nul
if errorlevel 1 goto :fail
xcopy /e /i /q /y actions "%OUT%\actions" >nul
if errorlevel 1 goto :fail
rem The sample configuration, without anyone's *.local.json overrides.
mkdir "%OUT%\config"
for %%F in (config\*.json) do (
    echo %%~nF | find /i ".local" >nul || copy /y "%%F" "%OUT%\config\" >nul
)
copy /y tinylib.ini.example "%OUT%\" >nul
copy /y README.md "%OUT%\" >nul
xcopy /e /i /q /y docs "%OUT%\docs" >nul
rem Never copy tinylib.ini: it points at this machine's security file.

echo.
echo === done: %OUT%\TinyLib.exe %VERSION%
dir /b "%OUT%"
set "RC=0"
goto :end

:fail
echo.
echo === BUILD FAILED
set "RC=1"

:end
rem keep the window open when started by double-click
echo %CMDCMDLINE% | find /i "/c" >nul && pause
endlocal & exit /b %RC%
