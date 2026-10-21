# Changelog

## Unreleased

### Player

- In and out points for footage: **I** / **O** set them at the current frame, **Alt+X** clears them, and the player plays and loops only from in to out. They are personal, stored in your preferences beside ratings, never in the library database.
- Collections keep their own in and out points per asset (copied from yours when an asset is added); a player opened from a collection uses and edits them, and collection export/import carries them. Collection files are now version 2; version 1 files still import.
- The frame-number overlay toggle moves from **O** to **F**.
- Copied and dragged paths, and the Copy paths action, use the in and out points as the frame range in the Nuke, AYON and Flame notations. Actions receive a `trim` function in their context.

### Interface

- The user name and groups move from the top bar to the window title, e.g. `TinyLib · Studio library · jan (admins, artists)`, and update on Refresh.
- The "TinyLib / Studio library" heading is removed from the top bar; the window title already shows it.
- Filters take two rows: the search beside the user name and main buttons; length, width, stars, tag picker, asset type and Clear filters together on the second row.
- "Fulltext" is renamed **Search**. The separate Tags field is removed: **Pick tag…** adds `{tags}:<tag>` to the search instead.
- The **Invert** checkbox is removed; exclude terms with `!` in the search (`!burst`, `{tags}!fire`).
- The window is split into framed panels on a darker background: filters, main buttons, libraries/categories, the asset view, collections, and asset details, so each area is recognisable at a glance.
- Buttons, group boxes and panels use the same corner radius as dropdowns.
- The tile and details views have no border of their own inside their panel, and tiles sit half as far apart (8 px instead of 16 px).
- Numeric filters: the labels are just **Length**, **Width** and **Stars**, with the units in their tooltips; the value boxes are narrower, have no spin arrows, and are as tall as the dropdowns beside them.
- Labels use sentence case throughout: **Create new library…**, **Library sort…** and **Rescan metadata…** (and their dialogs) match **Library crosscheck…**, and every label and progress message ends in a single `…` instead of `...`.
- The search box is as tall as the buttons beside it.
- Alternating rows in the Details view (and other lists) use a muted grey instead of near-white in the standalone style.
- **Cancel edit** appears directly to the left of the red **Write** button while editing, instead of at the bottom of the details panel.
- The main buttons panel is right-aligned in two rows: Access rights, Configuration and Refresh on top; Edit and Ingest asset below.
- Tiles, including the selected-tile highlight, have square corners and a crisp 1 px outline.
- A **Details** toggle beside **Collections** hides or shows the asset details panel. It is on by default and remembered; **Edit** turns it back on.
- The Libraries / Categories tree has no border of its own inside its panel; a thin line separates its heading.
- The mouse wheel scrolls the tile view one row of tiles per notch instead of a few pixels.
- Dropdowns show a down arrow (and **Library tools** a menu arrow), and are as tall as the buttons beside them, in both the standalone and the Nuke style.

### Security

- **Configuration…** is hidden for non-administrators (it was only greyed out). **Access rights…** and **Configuration…** re-read the security file when clicked and refuse non-administrators.
- Library tools (crosscheck, rescan metadata, sort, create library) re-read the security file and refuse non-administrators each time they run, not only when the window opens. Crosscheck and writing rescanned metadata now check this too; the button is hidden as soon as a refresh shows the account is no longer an administrator.
- The color space is stored only in the asset's **Color space** field. Ingest and metadata scans no longer add a `colorspace` metadata entry, and the copy in older assets is hidden on load and leaves the database the next time the asset is saved. An older asset without its own color space takes the metadata value.

### Deadline

- `deadline.web_service` submits through the Deadline Web Service REST API (`POST /api/jobs`), so no `deadlinecommand` process starts per job. Supports basic authentication and self-signed certificates. TinyLib falls back to `deadlinecommand` whenever the Web Service did not create the job: bad URL, no connection within 3 seconds, or an error answer. A lost answer after the request was sent stops instead, because the job may exist. In a folder batch only the jobs not yet confirmed fall back.
- Without the Web Service, a folder batch is submitted with one `deadlinecommand -SubmitMultipleJobs` call instead of one call per job.
- `.submission.json` records how the job was submitted (`method`).

### Processing

- A retried ingest job deletes the staging folder its failed attempt left behind and starts over, instead of stopping with "Staging exists from an earlier attempt". Deadline can now requeue a failed task on another worker. If the folder is still in use by another worker, the retry stops.
- The `vfx_transcode` backend makes the thumbnail and filmstrip of image-sequence footage in the same run as the proxy, so the frames are converted once. Previews taken from the proxy are unchanged.
- Fixed: the `vfx_transcode` proxy was encoded without the fit to 1920x1080, because `-ff_encode` lacked `{videofilter}`; footage that was not 16:9 failed validation.
- Fixed: 23.976 and 29.97 fps reach vfx-transcode as 24000/1001 and 30000/1001 instead of 2997/125 and 2997/100.
- The backend turns off burn-ins, slate, metadata, OIIO transform and smart token defaults from vfx-transcode's `presets.json`, makes anamorphic footage square-pixel, and writes intermediates to the worker's temp folder instead of the staged main sequence.
- `ocio` under `[tools]` in `tinylib.ini` sets the OCIO config for all processing profiles, so farm workers no longer need the `OCIO` environment variable. A worker without either now says so instead of reporting `${OCIO}` as an inaccessible file.
- Profiles may set `display`, `view` and `looks` for the backend, and `ocio_config` may be an `ocio://` built-in config.

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
