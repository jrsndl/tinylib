# Deployment

## Shared install

Install TinyLib once on a share that every workstation and farm worker can read, for example `//studio/pipeline/tinylib`. The folder holds `menu.py`, `worker.py`, `launch.py`, `launch_studio.bat`, the `tinylib` package and `tinylib.ini`.

- Give artists read-only access to the app folder and administrators write access. `tinylib.ini` chooses the security file, so artists must not be able to change it, and a local copy of the app folder defeats that (see [security setup](security.md#rules)).
- Copy `tinylib.ini.example` to `tinylib.ini` and set `security` and, usually, `config`. Use UNC paths rather than mapped drives.
- Point all users at the same install. Users never enter a library path; libraries come from the browser configuration.

## Configuration resolution

TinyLib reads two files: the **security file**, named only by `tinylib.ini`, and the **browser configuration** (libraries, profiles, Deadline). Executables come from `[tools]` in `tinylib.ini`. The browser configuration is the first of:

1. the path passed to `python launch.py --config <path>` or `tinylib.show("<path>")`;
2. the `TINYLIB_CONFIG` environment variable;
3. `config` in `tinylib.ini`;
4. `config/studio.json` in the app folder.

If a file named like the chosen one with `.local` before the extension exists (`studio.local.json` beside `studio.json`), it is used instead. A local file is a complete configuration, not a partial overlay; `config/*.local.json` is ignored by Git. `launch_studio.bat` adds nothing of its own: it runs `launch.py`, so the same order applies.

Every key is documented in the [configuration reference](configuration.md).

## Nuke 15.2 and newer

Add the folder that **contains** the `tinylib` package to the studio `init.py`. If the package is `//studio/pipeline/tinylib/tinylib/__init__.py`:

```python
nuke.pluginAddPath("//studio/pipeline/tinylib")
```

Do not add `//studio/pipeline/tinylib/tinylib` itself. That exposes files such as `tinylib/ui.py` as top-level modules and can shadow another plug-in's `ui` module.

No user `menu.py` entry is needed. Nuke loads the bundled `menu.py` from the registered folder and adds **TinyLib > Studio library** once. If the browser cannot open, the menu shows the error in a message box and prints the traceback to the Script Editor. Each Nuke session has one browser window; choosing the menu again brings it to the front. Edit locks are released when Nuke quits.

Set `TINYLIB_CONFIG` centrally to choose a different browser configuration for some users or shows; otherwise `config` from `tinylib.ini` is used.

TinyLib uses PySide2 in Nuke 15 and PySide6 in Nuke 16+. In terminal mode (`nuke -t`) TinyLib does not treat itself as hosted in Nuke.

For a quick test from Nuke's Script Editor without changing `init.py`:

```python
import sys
sys.path.insert(0, "<install folder>")
import tinylib
tinylib.show()                            # or tinylib.show("<path to a browser configuration>")
```

## Standalone browser

On Windows, double-click `launch_studio.bat`. It uses `.venv\Scripts\python.exe` when present, then `python` on PATH, then the Windows Python launcher (`py -3`). Startup errors stay visible in the console. Elsewhere, run `python launch.py`.

The standalone browser needs Python 3.9+ and PySide6 or PySide2 (`uv sync --extra pyside6`, or `pip install -e ".[pyside6]"`; see [development](development.md#packaging)).

## Local thumbnail cache

TinyLib stores downscaled thumbnails, filmstrips and normalized library indexes on the local machine, in a `tinylib` folder under the first writable location of:

1. `%NUKE_TEMP_DIR%`;
2. on Windows, a machine-shared `Temp` folder: `%SystemDrive%\Temp`, then `\Temp` on other fixed local drives (`D:` to `Z:`), then `%SystemRoot%\Temp`. Network, removable and optical drives are skipped without being accessed;
3. the user's temp folder.

The folder is chosen once per session. Hover over the browser status line to see it.

- A thumbnail's cache key includes the source path, file size and modification time, so a replaced library thumbnail is cached again automatically. Library media is never modified.
- A library index is keyed by the library identity plus the size and modification time of `tinylib_data.json`, so a changed database rebuilds it. **Refresh** bypasses and rebuilds the index, which helps after representation folders were changed by hand without updating `tinylib_data.json`.
- On startup, cache files unused for 60 days are deleted in the background, and folders from older cache versions are removed.

## Logging

TinyLib writes a rotating `tinylib-<user>.log` (1 MB, three backups) in the local cache folder, one per Windows user because the folder can be shared by everyone on the machine. The browser status-line tooltip shows its path. It records the configuration and security file in use, denied access, edit-lock changes and overrides, broken stale write locks, actions run, Deadline submissions and farm results. The farm worker also prints the same lines to the console, so they appear in Deadline's task log.

## Deadline 10.4

1. Deploy the app folder, including `worker.py` and `tinylib.ini`, to a farm-accessible share.
2. Set `deadline.worker_script` and `deadline.spool_root` in the studio browser configuration to existing shared paths. They are empty in the public `config/studio.json` until the studio chooses them.
3. Make sure the Deadline Python plugin has the configured Python version (`deadline.python_version`, default 3.10). Set `pool`, `group` and `priority` as needed.
4. Workers take tool paths from `[tools]` in **their own** `tinylib.ini`, and processing profiles from their own browser configuration (`config` in `tinylib.ini`, or `TINYLIB_CONFIG` on the worker), never from the job manifest. Set every tool and the OCIO config to paths that exist on the workers; if the farm and workstations need different tool paths, give the farm its own install folder with its own `tinylib.ini`.
5. Give the worker account read access to source media and the spool, and write access to destination libraries. Do not make it an administrator; give it a `farm` group with view and ingest grants (see [security setup](security.md#farm)). Prefer UNC paths over mapped drives. This targets a Windows farm; cross-platform path mapping is not implemented.
6. Use **Submit to Deadline** in the ingest form. One Python task copies main and highres, generates and validates the previews, and publishes the asset. The dialog shows the Deadline job ID and manifest location once Deadline confirms (within 60 seconds). For faster submission, run the Deadline Web Service and set [`deadline.web_service`](configuration.md#web_service).

Deadline Monitor shows processing output. Beside each manifest in the spool, `.submission.json` stores the Deadline job ID and `.status.json` records processing, completion or failure. Refresh the browser after a job completes; the browser does not poll Deadline.

### Farm worker

`worker.py <manifest.json>` processes one manifest. It also runs locally for a manifest saved with **Save job manifest**.

- It checks its own account's view and ingest grants for the destination library and refuses read-only libraries. It cannot verify which artist submitted the job, so `ingest_user` in a manifest is informational.
- Files are staged in `<library>/.tinylib-staging/<job-id>`. Publication takes the library's exclusive write lock, rereads the database and appends the asset atomically (see [write locks](admin.md#write-locks)).
- A failed job keeps its staging folder and is not registered. A retry, on any worker, deletes that folder and starts over, so Deadline can requeue failed tasks on other workers. A completed manifest can be retried without creating a duplicate.
- Every external tool call has a timeout: probes 60 seconds, each conversion step one hour.
- Set `OCIO`, or `ocio_config` in the processing profile, to an ACES configuration that contains the profile's color space names.

On network storage, validate the server's exclusive-create and rename behavior before running several workers at once.

## Performance configuration

To measure loading and search without other libraries, create `config/performance.local.json` with a single read-only library and run:

```powershell
python launch.py --config config/performance.json
```

`config/performance.json` itself need not exist; the `.local` file is used in its place. `tests/qt_performance.py` uses the same configuration and writes its results to `artifacts/features-performance-e.json`.

Initial read-only measurements for 1,543 assets were 3.650 seconds for the first load and 0.187 seconds for the next load (from the index cache); searching for `fire` took 17.13 ms and 7.15 ms. These timings cover database loading, media-path discovery and filtering, not thumbnail decoding or playback.
