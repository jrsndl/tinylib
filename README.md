# TinyLib

TinyLib is a studio asset library for VFX footage, stills, models and other assets. It runs inside Nuke or as a standalone Qt browser, with search, filters, collections and a review player. Ingest jobs run as Deadline tasks that generate previews and publish into shared libraries. Access is controlled by Windows identity and an admin-owned security file.

## Quick start

Python 3.9+ and PySide6 or PySide2 are needed for the standalone browser. Inside Nuke, Nuke supplies Qt.

1. Install the dependencies in a virtual environment, from the app folder:

   ```powershell
   uv sync --extra pyside6
   ```

   or, without uv:

   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install -e ".[pyside6]"
   ```

   Use the `pyside2` extra instead for Nuke 15's Python.

2. Copy `tinylib.ini.example` to `tinylib.ini` beside `menu.py` and set `security` (and optionally `config`), and the paths of ffmpeg, ffprobe, oiiotool and vfx-transcode under `[tools]`. For a local trial, use the commented lines at the bottom of the example, which point both at `config/studio.json`.

3. Add yourself as the first administrator. Without this, TinyLib starts with no access.

   ```powershell
   python -m tinylib.admin whoami
   python -m tinylib.admin add-user "<your account>"
   ```

4. Start the browser: double-click `launch_studio.bat` on Windows, or run `python launch.py` (`python launch.py --config path/to/studio.json` for another browser configuration).

5. For Nuke, add the folder that **contains** the `tinylib` package to the studio `init.py`, not the `tinylib` folder itself:

   ```python
   nuke.pluginAddPath("//studio/pipeline/tinylib")
   ```

   The bundled `menu.py` adds **TinyLib > Studio library**.

`python launch.py --demo` opens `config/demo.json`. Neither that file nor the sample libraries it reads (`testdata/`) are in the repository; provide both locally to use it.

## Documentation

- [User guide](docs/user-guide.md): browsing, search, filters, views, collections, the player, editing and ingest.
- [Administration](docs/admin.md): library tools, edit and write locks, legacy databases.
- [Deployment](docs/deployment.md): Nuke and shared installs, the local cache, Deadline and the farm worker, logging.
- [Configuration reference](docs/configuration.md): every key in `tinylib.ini`, the browser configuration and the security file.
- [Security setup](docs/security.md): the security file, Windows permissions and the first administrator.
- [Studio actions and access](docs/actions-and-access.md): groups, grants and Python action packages.
- [Asset types and metadata](docs/asset-types.md): types, representations, extension groups and fields.
- [Development](docs/development.md): tests, CI, linting and packaging.
- [Changelog](CHANGELOG.md)
