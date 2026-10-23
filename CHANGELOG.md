# Changelog

## Unreleased

### Access

- New library capability **copy** (default: users, managers, admins), in `access.defaults`, `libraries[].permissions` and **Access rights…**. Without it, assets of that library cannot leave TinyLib: a drag to another application carries no paths (dragging into collections still works), **Ctrl+C** in the views, collection **Copy** and **Ctrl+C** of a player frame are refused, and actions declaring `"permissions": ["copy"]` (Copy main paths, Copy Assets) are not offered. Give a group `view` without `copy` to let it browse but not copy. Existing security files need no change; accounts that are only in `restricted` get no `copy` unless granted.
- Action manifests accept `permissions`: library capabilities the action needs besides `actions`.

### Ingest

- **Transcode main** for Footage: the farm converts the main to an image sequence that becomes the asset's main, for stock footage received as Rec.709 MOV or MP4. Fields: extension (`exr`), compression (`dwaa`), start frame (`1001`) and output color space (`ACEScg`). It is ticked automatically when the main is not an image sequence; in a folder batch it applies to the mains that are not sequences. FFmpeg decodes a movie to 16-bit frames, oiiotool converts them from the main color space with the profile's OCIO config. Studio defaults: `transcode_main` in the browser configuration.
- **Main color space** moves up, just below **Main** in the ingest form.
- Transcode main: **Copy main source** (on by default) also keeps the original file or sequence unchanged in the asset's main folder, recorded as the new **main-original** part (Footage). An alpha channel in the main is detected (ffprobe pixel format) and kept: RGBA frames, color converted unpremultiplied.
- Fixed: footage processing failed with FFmpeg 8 and newer (`Unrecognized option 'vsync'`); it uses `-fps_mode`, and `-vsync` only on FFmpeg older than 5.1.

### Actions

- Import main / highres into Nuke no longer stops when the asset's color space is not in the Nuke project: the Read node is created with Nuke's default color space and the status line names the assets to fix (Copy Assets' Read nodes too). Color spaces also match ignoring case and OCIO menu folders (`Roles/…`, `ACES/…`).
- New bundled action **Open in Player** (`player.open`, category Viewers): opens footage and stills in an external player chosen from the tools in `tinylib.ini` (all but TinyLib's own). From the dropdown it shows its settings (player, per-asset arguments with tokens such as `{rv}` and `{first_file}`, arguments before the assets, component, in/out, one player or one per asset, a command preview), saved in your user preferences; from the right-click menu it opens the assets with them directly. Defaults per player: RV `{rv}`; DJV `{first_file}`, plus `-timeUnits Frames -inPoint {first} -outPoint {last}` (new field **In/out arguments (one asset)**) when it opens one asset with in/out points; mrv2 `{first_file}`, plus `-inOutRange {first}/{count}/{fps}` when it opens one asset with in/out points; others `{first_file}`. xSTUDIO `{xstudio}` (`name.####.exr=1001-1100`). New argument tokens `{count}`, `{fps}` and `{xstudio}`.
- Open in Player's settings can be opened at any time: **Open in Player settings…** at the end of both action dropdowns and of the right-click menus, with or without a selection. Manifest `"settings": true` gives any action such an entry (`context['invoked'] == 'settings'`, no assets).
- **Right-click menus** in the main view, the collection panel and the player windows list the actions whose manifest has `context_menu` (`true`, or `{"label", "where": ["main", "collection", "player"]}`), filtered like the dropdowns. Action context gains `invoked` (`dropdown` or `context_menu`), `place` and, from a player, `window`.
- **External tools for actions**: any name under `[tools]` in `tinylib.ini` (e.g. `rv`, `djv`, `mrv2`) can be run by actions through `tinylib.external_tools` (`resolve`, `launch`, `asset_arguments`): started detached, without a shell, and without the host's Python and Qt environment variables. Manifest `tools` lists the programs an action needs; it is offered only when they are found.
- Fixed: files copied by Copy Assets (and picked thumbnails) were hidden in Windows Explorer on Samba shares. Their temporary names started with a dot, which Samba marks hidden (`hide dot files`), and the mark stayed after the rename. Temporary names no longer start with a dot, a hidden mark is cleared after writing, and the rename is retried while the share still holds the new file.
- Copy Assets in Nuke: new Read node option **Set colorspace**, off by default (config `nuke_colorspace`); off, Nuke chooses the Read's color space.
- Copy Assets' path template defaults to `./resources` in Nuke (`.` standalone), and Nuke and standalone each remember their own (config keys `template_nuke`, `template`).
- New bundled action **Copy Assets** (`copy.assets`, category General), in Nuke and standalone: copies the main, highres or proxy of the selected assets (or of a collection's selection, or a whole collection) into a folder given by a path template relative to a base folder (the Nuke script's folder in Nuke; `..`, `{date}`, `{collection}` and environment variables allowed). Options: a subfolder per file sequence (clean name), only the frames from in to out, keep or overwrite existing files, and in Nuke Read nodes for the copies with frame mode left as is, start at the script start frame, or an empty expression. Checked before copying, copied with progress and Cancel, each file written under a temporary name. See the user guide.
- Manifest `asset_filter.whole_view: false` keeps an action off the properties dropdown while nothing is selected. Action context gains `whole_view` and, for collection runs, `collection_selected`.
- New bundled action **Publish to AYON** (`ayon.publish`, category AYON), in Nuke and standalone: publishes the selected footage (product type `render`) and stills (`image`) to a picked AYON folder and task, through AYON's traypublisher CSV ingest. Its **Components** section chooses which of main, proxy and highres become representations (main and proxy by default), with each one's representation name, tags and colour space; the thumbnail is the version thumbnail. Each asset's variant (default: its name), version (default: its version number, or 1) and comment are editable. In Nuke started from AYON it uses the session's AYON credentials and context; otherwise you sign in to AYON in the window (session token kept in the Windows Credential Manager). With ftrack credentials (environment or sign-in) the reviewable is also published to ftrack; without them the `ftrackreview` tag is left out. See [docs/ayon-publish.md](docs/ayon-publish.md).
- Publish to AYON has **Deadline Publish** (the ingest command runs as Deadline CommandLine jobs, submitted through the Web Service or `deadlinecommand` like ingests, with the CSV files in a farm-readable folder) and **Each Asset Separate** (one CSV file and one publish or job per asset). New config keys `deadline_csv_folder`, `deadline_ayon_executable`, `deadline_send_credentials`, `deadline_environment` and `deadline`.
- New `ayon` extra (`ayon-python-api`, `keyring`) for the action outside AYON; `launch_studio.bat` and `build.bat` install it, and `TinyLib.exe` includes it. New `[tools]` key `ayon_console`.

### Library tools

- After Library sort, Labels from names, Keyword cleaner, Category change and Create new library, only the library they changed is read again; the other libraries stay as loaded. **Refresh** still reads every library.
- **Category change…** (also in edit mode): moves the selected assets of one library to an existing or new category, renaming each asset folder on disk and updating category, id and paths in one database write under the edit lock. All checks run before anything moves (still in the database as loaded, plain `<category>/<name>` folder without links, every file inside it, no shared folder, target free); a failed rename or database write moves the folders back. Your ratings, in/out points and collection entries follow the moved assets.
- **Keyword cleaner…** cleans the keywords of one library, or only of its selected assets: removes number-only keywords and applies three whole-keyword replacements (empty removes, commas split), and adds comma-separated keywords to every asset in scope, with a live preview.
- **Labels from names…** makes asset labels from asset names in one library: only empty labels (default) or all, split words at capitals, underscores and dashes to spaces, and three search-and-replace pairs, with a live preview. Writes under the standard edit lock.

### HDRI (lat-long stills)

- A Still with `is_equirectangular` is a lat-long still (HDRI). The ingest form has a **Projection: Lat-long** checkbox for it, ticked automatically for a 2:1 EXR or HDR main. Its main must be EXR or HDR; the highres is optional and copied as it is.
- Radiance `.hdr` is a Still extension (`extensions_stills`). A studio configuration that overrides `extensions_stills` needs `hdr` added.
- Lat-long proxies and thumbnails are tonemapped 2:1 JPEGs. Generated ones are 4096 × 2048 (profile key `latlong_proxy_size`) and 960 × 480; supplied ones may be any 2:1 size, and one that is not 2:1 fails the ingest.
- A Still's proxy is no longer checked against 1920 × 1080.
- New 360 viewer for lat-long stills: look around from inside the sphere (drag, wheel for field of view), toggle between the proxy and the HDR main (button or Tab), set exposure and gamma on the main (sliders neutral in the middle; click a caption to reset it), flat view (F) with zoom and pan, linear pixel readout. **Use highres if available** (off by default) shows the highres instead of the main. The exposure and gamma sliders appear only for the HDR image. The main and highres views are not color accurate: no OCIO conversion or display transform, only exposure, gamma and the sRGB curve. OpenGL; the main is decoded in the background and downsized above `pano_max_width` (8192). Tiles of lat-long stills show a **360** badge.
- The 360 viewer reads the main with FFmpeg in the main's own pixel format (for example `gbrapf16le` for a half-float EXR), never converted: FFmpeg's pixel-format conversion clamps floats to 1.0, which showed as clipped highlights when exposure was lowered. oiiotool reads a main FFmpeg cannot.
- Folder ingest also works for Stills, in separate and same folder structure: every image file is an asset (numbered files are not joined into sequences), with proxy, highres and thumbnail paired as for footage. **Projection: Lat-long** flags the whole batch; then only EXR and HDR files are mains.
- Folder ingest: a same-folder-structure regex also matches the path relative to the main folder (`/proxy/`), so HDRIs laid out one folder per asset with `main`, `proxy`, `thumb` and `highres` subfolders pair. In separate folder structure, component folders may sit inside the main folder, and their fields offer only **Folder…**.
- Folder ingest: the Proxy, Thumb and Filmstrip rows choose **Paired, else generate** (default), **Supply (paired)** (every main must have one) or **Generate** (paired files ignored).
- numpy is now part of the `pyside6` and `pyside2` extras (the 360 viewer decodes HDR mains with it; Nuke ships its own).
- `launch_studio.bat` runs `uv sync --extra pyside6 --inexact` first when uv is installed, so missing packages (such as numpy) are installed before the browser starts.

### Packaging

- `build.bat` freezes the standalone browser into `dist\TinyLib\TinyLib.exe` with uv and PyInstaller, for machines without Python. A frozen exe reads `tinylib.ini`, `config\` and `actions\` from its own folder. PyInstaller is in a new `build` dependency group.

### Player

- **Set as thumbnail** in the footage player's bottom bar (beside Loop): the stopped frame replaces the asset's thumbnail file, at the old file's size and file type, with the proxy's padding cropped and the picture fitted like generated thumbnails. For administrators and users with the library's edit or ingest permission; not in read-only libraries.
- **PgUp / PgDn** in the review player and the 360 viewer open the previous / next asset in place, keeping the play state (playing or stopped, direction, speed, Loop, overlay) and the 360 viewer's flat view, proxy/HDR, highres and grading: from the current collection, the collection's assets; from the main view or the Properties panel, what the main view shows (filtered and sorted). Both ends wrap.

- In and out points for footage: **I** / **O** set them at the current frame, **Alt+X** clears them, and the player plays and loops only from in to out. They are personal, stored in your preferences beside ratings, never in the library database.
- Collections keep their own in and out points per asset (copied from yours when an asset is added); a player opened from a collection uses and edits them, and collection export/import carries them. Collection files are now version 2; version 1 files still import.
- The frame-number overlay toggle moves from **O** to **F**.
- Copied and dragged paths, and the Copy paths action, use the in and out points as the frame range in the Nuke, AYON and Flame notations. Actions receive a `trim` function in their context.

### Interface

- **Preferences…** in the user menu: Paths (moved there from the main view's controls; default Nuke), Show Properties panel on start (on), Show Collections panel on start (off), Remember library and category (on: TinyLib opens on the library or category selected last time), Show Info (on) and Tile text. The Properties button no longer saves its state; the start setting decides.
- The default tile text is `{name}\n{width} × {height} {length} s` (was `{name}\n{type} · {width} × {height}`). A tile text you or the studio set is kept.
- A user menu in the bottom-left corner shows your login name without the domain (e.g. `jan` for `studio\jan`); it lists your full account and groups. For administrators it holds **Library tools**, which moved there from beneath the library tree.
- Admins can **Login as…** another user from the user menu: TinyLib then behaves as it does for that user until **Log out** in the same menu. Edit locks, ingests and the log record both accounts (`studio\admin as studio\jan`).
- The action dropdowns remember the last action you picked, in your user preferences (`last_action` for the properties panel, `last_collection_action` for the collection panel). It is preselected whenever it is available for what the dropdown would run on.
- The status line shows how many assets are selected (main view or collection, whichever has focus) when any are, e.g. `120 assets / 4000 total · 0 collected · 3 selected`. The count of assets in collections reads "collected" instead of "picked".
- The properties panel's action dropdown also works with nothing selected: actions then run on every asset the main view shows, after a confirmation, as the collection dropdown runs on the whole collection.
- The properties panel shows a read-only **Parts** row below Type: the asset's components and file types, main first, e.g. `main:exr highres:exr proxy:jpg`. Thumbnail and filmstrip are not listed.
- The Stars filter is the same five stars as **Rate selection**: click a star to show assets with at least that many stars, click it again or **Any** for every rating. It replaces the operator and number.
- Fixed: the Length, Width and Stars numbers still could not be typed into. They are plain number fields now instead of spin boxes; clearing a number sets the rule back to Any.
- Tiles show the star rating in the top right corner of the preview (rated assets only, also with Info off), instead of a star row under the caption. Captions are shorter by that row.

- Fixed: the Length, Width and Stars numbers could not be typed into until an operator was chosen. They are always editable now; typing a number while the operator is Any selects `>` (length, width) or `=` (stars).
- The **Details** button that shows or hides the asset panel is renamed **Properties**, so it is no longer confused with the Details view mode.
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
- **vfx-transcode is the default for EXR image sequences in every profile.** It was used only by profiles with `"proxy_backend": "vfx_transcode"`, and the ingest form preselects the first profile, so most ingests converted every frame with a separate oiiotool run. Profiles without `proxy_backend` now use it whenever vfx-transcode is installed on the worker and their `color_args` are the plain colourspace conversion; otherwise, and for `"proxy_backend": "builtin"`, the built-in path is used. `"vfx_transcode"` still forces it and now fails on custom `color_args` instead of ignoring them. DPX, TIFF and PNG sequences always use the built-in path, because vfx-transcode does not colour-convert them. The sample `studio.json` drops its separate "VFX Transcode — Rec.709" profile, which is now the same as "ACES to Rec.709". A queued job that names a profile fails on a worker whose configuration no longer has it.
- The backend runs vfx-transcode with a presets file of TinyLib's own, written for each job, so studio defaults in vfx-transcode's `presets.json` can no longer reach library previews.
- Frames are converted straight to the size the proxy shows inside 1920x1080. Footage taller than 16:9 was converted 1920 wide and then scaled down again.
- The thumbnail is made at its fitted size as PNG and only padded to 960x506, instead of a 1280-wide JPEG that was resized and compressed a second time.
- With previews taken from the proxy, the filmstrip is read from the encoded proxy in the same run, in one decoding pass instead of 24 FFmpeg runs that each decoded the proxy up to their frame.
- With a supplied proxy and `thumbnail_frame` `largest`, the thumbnail is the largest source frame, as in the built-in path. vfx-transcode had silently used the middle frame, since it compares converted frames and none were converted.
- The worker probes the first main frame once and reuses it for the asset metadata.

### Editing

- Assets can have a **label**, edited in place of the panel heading while editing one asset (prefilled with the name; never multiedited). The browser, players and Nuke Read nodes show the label instead of the name; the **Name** field and the asset folder keep the ingest name. New tile token `{ingest_name}`; `{name}` shows the label. Search matches labels too.
- **Delete** in edit mode (administrators only) marks the selected assets for deletion; **Write** shows them in a confirmation window, where each can be unchecked, and deletes them from the database only (default) or from storage and the database. Storage deletion removes only an asset's own `<library>/<category>/<name>` folder and lists any files it kept.

### Ingest

- Footage follows three scenarios. A main-only EXR gets proxy, thumbnail and filmstrip from one vfx-transcode run. With a supplied proxy, thumbnail and filmstrip come from the proxy by FFmpeg, without vfx-transcode. A main-only display-referred main (a movie, or a PNG, TIFF, DPX or JPG sequence) is converted to scene-linear EXRs (profile key `working_colorspace`, default `ACES - ACEScg`; movies via 16-bit PNGs) and then goes through vfx-transcode. Before, display-referred mains used the per-frame built-in path. The highres is never converted.
- The "Generate thumb / strip from" choice is removed: thumbnail and filmstrip come from the proxy exactly when one is supplied.
- A long filmstrip from a supplied proxy is made in one FFmpeg pass instead of 24.
- Folder ingest pairs existing proxies, highres, thumbnails and filmstrips with their mains, from separate folders (by file name: equal, or starting with the main's name) or from the same folder (by a regex per component). Before, every component was generated and highres was skipped.
- The main category can come from the main file's folder, 1, 2 or 3 levels up, besides picking an existing category or creating one. In a folder batch it is resolved for each main, so one batch can fill several categories.

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
