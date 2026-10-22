# Development

## Packaging

`pyproject.toml` describes the `tinylib` package (version 3.1.0, Python 3.9+). It has no required dependencies, because Nuke supplies Qt. Optional extras:

| Extra | Installs | For |
| --- | --- | --- |
| `pyside6` | `PySide6>=6.5` | Nuke 16+ and new standalone installs |
| `pyside2` | `PySide2>=5.15` | Nuke 15 (PySide2 has no wheels for Python above 3.10) |
| `ayon` | `ayon-python-api>=1.0`, `keyring>=24` | [Publish to AYON](ayon-publish.md) outside an AYON-launched Nuke; `launch_studio.bat` and `build.bat` install it |
| `dev` | `ruff>=0.6` | Linting |

```powershell
uv sync --extra pyside6 --extra dev
```

or with a plain virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[pyside6,dev]"
```

`launch_studio.bat` uses `.venv\Scripts\python.exe` when it exists. The version in `pyproject.toml` must match `tinylib.__version__`, and `CHANGELOG.md` must have a `## <version>` heading; `tests/test_packaging.py` checks both.

## Tests

Unit tests:

```powershell
python -m unittest discover -s tests
```

They need no sample media or conversion tools. Tests that need Qt are skipped, not reported as errors, when neither PySide6 nor PySide2 is installed.

Script tests (Qt, player and real conversions):

```powershell
python tests/run_scripts.py              # every tests/qt_*.py and tests/*_smoke.py
python tests/run_scripts.py qt_smoke     # only the named scripts
```

Each script runs in its own process with `QT_QPA_PLATFORM=offscreen` and its result is summarized at the end. All of them need PySide. Some need local files that are not in the repository:

| Script | Also needs |
| --- | --- |
| `qt_smoke.py`, `qt_features.py` | `config/demo.json` (or `demo.local.json`) and the sample libraries in `testdata/` |
| `qt_performance.py` | `config/performance.local.json` naming a read-only library |
| `processing_smoke.py`, `vfx_smoke.py` | `testdata/` sample media, oiiotool, FFmpeg and an OCIO config; `vfx_smoke.py` also vfx-transcode |
| `player_smoke.py`, `qt_player.py` | FFmpeg (and ffplay for `player_smoke.py`) |
| `qt_pano.py` | `testdata/hdrilib`, FFmpeg and OpenGL. On Windows its window appears briefly, because Qt's offscreen platform has no OpenGL there; `python tests/qt_pano.py --show` keeps it open to try by hand. |

Without these, the runner reports the script as `skip` with what is missing, instead of running it. Do not run the scripts with `python -O`; they check results with `assert`.

The conversion tests use only sample inputs and write into unique folders under `artifacts/`; they never submit farm jobs. Screenshots also go to `artifacts/`. The feature test exercises Ctrl-click selection, multicolumn lists, view switching, filters, star clicks, Qt drag and drop, collection actions and persistence across a browser restart, using temporary user preferences. The performance test reads the configured library, also with temporary preferences. Live Nuke Read-node imports and a real Deadline job still need studio validation.

Sample media, conversion binaries (`executables/`), the old scripts (`oldscripts/`) and generated `artifacts/` are not distributed and are ignored by Git. So are `tinylib.ini` and `config/*.local.json`.

### Standalone tests with Nuke's Python

To run the tests with Nuke 16's Python and Qt, set `PYTHONPATH` to its `pythonextensions/site-packages`, `QT_PLUGIN_PATH` to its `qtplugins` folder and `QT_QPA_PLATFORM_PLUGIN_PATH` to `qtplugins/platforms`. `run_scripts.py` sets `QT_QPA_PLATFORM=offscreen`. These overrides are for testing only, not for Nuke startup.

## Linting

```powershell
ruff check .
```

The configuration in `pyproject.toml` checks correctness only (syntax errors, undefined names, unused imports and variables) with a 130-character line limit. Script-style tests and `worker.py` may put `sys.path` setup before imports.

## Continuous integration

`.github/workflows/tests.yml` runs on every push and pull request, on Windows and Linux with Python 3.9 and 3.11. It installs PySide6 and ruff, runs `ruff check .`, then the unit tests with `QT_QPA_PLATFORM=offscreen`. The script tests are not run in CI because they need local sample data and tools.

## Code layout

| Module | Purpose |
| --- | --- |
| `storage.py` | Atomic JSON writes and cross-machine write locks. `library.py` re-exports `read_json`, `atomic_json` and `library_lock`. |
| `media_probe.py` | Running ffprobe, ffmpeg and oiiotool with timeouts; parsing their numbers. |
| `log.py` | The rotating `tinylib-<user>.log`; modules log through `get_logger(__name__)`. |
| `settings.py`, `install.py` | Loading and validating the browser configuration; reading `tinylib.ini`. |
| `access.py`, `admin.py` | Grants from the security file; the `python -m tinylib.admin` command. |
| `processing.py`, `ingest.py` | Farm worker and Deadline submission. |

See [processing notes](history/processing-notes.md) for the audit of the scripts TinyLib's processing replaced.
