# Changelog

## 3.1.0

### Security

- `tinylib.ini` in the app folder names the studio security file. Only it can choose that file; `TINYLIB_CONFIG`, `--config`, `*.local.json` and the old `security_config` key cannot.
- TinyLib fails closed: a missing or invalid `tinylib.ini` or security file, or a security file without users, grants nobody access and the status line says why.
- No automatic first administrator. Add one with `python -m tinylib.admin add-user`; the same command also lists, adds and removes accounts.
- The farm worker uses tool paths and processing profiles from its own configuration, never from the job manifest.
- Tool paths (ffmpeg, ffprobe, ffplay, oiiotool, vfx_transcode) move to a `[tools]` section of `tinylib.ini`, which overrides `tools` in any browser configuration. Bare names are looked up on PATH; relative paths resolve beside `tinylib.ini`.
- `read_only` in the security file wins over the browser configuration.
- `{app}` prefix for `action_roots` and `tools` paths, resolved inside the install folder.

### Fixes

- A stale `.tinylib-write.lock` is broken automatically: at once when its owner process is gone on the same computer, otherwise after 10 minutes. The timeout message names the computer and process holding the lock.
- Atomic JSON writes retry while Windows readers briefly hold the file open.
- Publishing an ingest patches the current database instead of rewriting it from a possibly stale local index, so other users' edits and unknown keys are kept.
- A damaged `preferences.json` is set aside (`preferences.damaged-<date>.json`) instead of preventing startup; a temporarily unreadable one is left untouched.
- Frame rate is stored as `metadata.frame_rate` everywhere. Legacy `FPS` values are migrated to `frame_rate` on load, Rescan Metadata compares `frame_rate`, and the tile `{fps}` token and the player use it.
- Houdini path notation pads the frame number (`$F4`).
- A pixel aspect of `0:1` or `N/A` from ffprobe no longer produces 0 or a crash; it is read as 1.0.
- Image-sequence frame rate and duration come from the ingest FPS and frame range, not from probing a single frame.
- Rescan Metadata writes `_meta.json` sidecars beside `main/`, not inside it. Old sidecars inside `main/` are ignored by folder scans.
- `ingest_date` defaults to empty instead of `20251224`.
- All external tools have timeouts: probes 60 s, conversions 1 h.
- The local cache folder probes only fixed local drives and is chosen once per session. Cache entries unused for 60 days and old cache versions are removed.
- A search with unbalanced quotes in `{field}:'…'` no longer disables filtering.
- Filtering can no longer get stuck after an error.
- One browser window per session: the Nuke menu raises the existing window. Edit locks are released when Nuke quits.
- The Nuke menu shows errors when TinyLib cannot open.
- **Esc** in Crosscheck and Rescan Metadata cancels the scan instead of leaving it running.
- Review player: works with FFmpeg 4.x (falls back to `-vsync`), tolerates clips shorter than estimated, and retries after an error when seeking.
- The preview cache no longer thrashes when many previews are visible.
- Ingest form: keeps a typed name and keywords, has no `foo` default category, is non-modal so submission windows stay usable, and builds folder manifests off the GUI thread.
- Library load errors are kept after saves.
- **Refresh** no longer closes review players.
- Preview read errors never leave previews pending.
- Deleted collections are handled.
- **Access rights…** rejects rows without an account name and asks before you remove your own admin rights.
- Multiedit shows differing values as placeholders, so edits replace values instead of joining them.
- A failed edit-mode activation no longer repeats its error dialog.
- The crosscheck report name typo is fixed (`_crosscheck_`).
- `launch.py --help` works without PySide installed.
- Browser configuration is validated, with messages naming the file and key.
- An action manifest's `callable` must be a Python function name.

### Changes

- Houdini path notation is `$F4` (padding matches the sequence) instead of `$F`.
- Metadata key `frame_rate` replaces `FPS`.
- Rescan Metadata sidecars are written beside `main/`.
- Multiedit starts differing text fields empty with the values as placeholders.
- The ingest form is non-modal.

### Developer

- New modules: `storage.py` (atomic writes and write locks), `media_probe.py` (tool calls with timeouts), `log.py` (logging).
- Qt unit tests are skipped, not errored, without PySide.
- `tests/run_scripts.py` runs all `qt_*.py` and `*_smoke.py` scripts and summarizes the results.
- `pyproject.toml` with `pyside6`, `pyside2` and `dev` extras (`uv sync --extra pyside6`).
- Ruff configuration.
- GitHub Actions CI on Windows and Linux with Python 3.9 and 3.11.
- Rotating `tinylib-<user>.log` in the local cache folder (one per Windows user, because that folder can be shared); its path is shown in the status-line tooltip. The farm worker also logs to the console.
- Version 3.1.0.

## 3.0.0

Previous release.
