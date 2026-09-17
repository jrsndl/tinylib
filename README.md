# TinyLib

A Nuke asset browser and Deadline ingest worker for studio footage and HDRI libraries.

## Try the sample libraries

```powershell
cd C:\tools\tinylib
python launch.py --demo
```

The demo reads `testdata/demolib/data.json`, remaps its old legacy absolute paths, and discovers the three flat HDRI asset folders. It does not rewrite the sample database. Some database entries have no corresponding sample files and show “Preview unavailable”. Both demo libraries are read-only.

For a production browser, run `python launch.py` or load it inside Nuke. Python 3.9+ and the matching PySide binding are required; Nuke supplies its own Qt runtime.

Configure a read-only library in `config/performance.local.json` to isolate performance testing from other libraries:

```powershell
python launch.py --config config/performance.json
```

This dedicated configuration isolates one library. Initial read-only measurements for 1,543 assets were 3.650 seconds for the first load and 0.187 seconds for the next load; searching for `fire` took 17.13 ms and 7.15 ms. These timings cover database loading/media-path discovery and filtering, not thumbnail decoding or playback. Results are saved in `artifacts/performance-e.json`.

## Nuke 15.2 and newer

Add this to the studio `init.py`:

```python
nuke.pluginAddPath("C:/tools/tinylib")
```

The `TinyLib > Studio library` menu opens the browser. A studio deployment should use the same shared installation path for all users. Set `TINYLIB_CONFIG` centrally to a shared JSON configuration; otherwise the installation's `config/studio.json` is used. Users do not need to enter a library path.

For an immediate test in Nuke's Script Editor:

```python
import sys
sys.path.insert(0, "C:/tools/tinylib")
import tinylib
tinylib.show("C:/tools/tinylib/config/demo.json")
```

The browser uses PySide2 for Nuke 15 and PySide6 for Nuke 16+. Main and highres imports create Read nodes with explicit frame ranges and color space; an unknown project color space is reported instead of silently selecting a different one. Still previews preserve image aspect ratio. Double-click an asset for JPEG/MP4/MOV preview playback; hover footage for a 24-frame filmstrip.

## Search and libraries

Each configured library has a name, root folder and optional `legacy_roots` mappings. Add more entries to the `libraries` array in `config/studio.json`. `read_only: true` excludes a library from ingest destinations. Refresh rereads library data; restart the browser after changing configuration.

Search is case-insensitive across name, category, library and keywords. Multiple words must all match. Use quotes for a phrase, `tag:smoke` for an exact keyword, and `-night` or `-tag:night` to exclude matches. Library, media type, category and keyword filters combine with search.

The left panel is an expanded tree of libraries and their categories. Select a library to browse all its categories, a category to narrow that library, or “All libraries” to search across everything.

The filtering panel combines fulltext search, an **Invert** checkbox (applies only to fulltext), comma-separated exact tags (all must match), media type, duration in seconds, pixel width, and star count. Numeric filters offer `>`, `<`, and `=`; select **Any** to disable a rule. Duration is frame count divided by FPS, including fractional FPS. Stills have duration zero. Missing duration/width metadata is excluded only when that numeric filter is enabled. “Clear filters” preserves the selected library/category.

## Main-view controls and collections

- **Tiles** is the default, with thumbnails and hover filmstrips. **Details** presents rows with a tiny preview and columns for name, library, category, media type, duration, dimensions, stars and keywords. **List** shows asset names. Selection is preserved when switching views.
- Use Ctrl-click, Shift-click or Ctrl+A for multi-selection. Import main/highres and star-rating actions apply to the selected assets. The properties panel summarizes multi-selection; opening a preview requires one selected asset.
- **Play all / Stop all** animates the available filmstrips together in tiles and details. Only visible previews are decoded and painted; newly scrolled-in assets join the shared animation position. List mode remains text-only. Stopping restores thumbnails; ordinary hover previews remain available.
- The five stars set the rating on the entire selection. **Clear** sets zero stars. Mixed ratings show five unfilled stars until a value is chosen. Card size is in this top control panel and applies to tiles.
- **Collections** opens the scratchpad between the main view and properties. It starts hidden every time. Create collections with **New** (collection01, collection02, …), rename them, or delete them. Deleting a collection does not delete any media.
- Drag a main-view selection onto the collection list. If there are no collections, the first one is created automatically. Duplicate picks in one collection are ignored. Picked assets disappear from the main view, including when the collections panel is closed. Remove a pick or delete its collection to restore it; an asset remains hidden if another collection still contains it.
- Collections are independent of the current search or category. Select items there to preview/import/rate them. Unavailable assets remain listed with an unavailable label so references are not silently lost.

Stars and collection references are saved per user at `%APPDATA%/TinyLib/preferences.json` on Windows (`~/.config/TinyLib/preferences.json` without APPDATA). Override the path with `TINYLIB_USER_PREFS` if needed. Saves are atomic and reread existing preferences under a lock to preserve edits from other browser windows. Library databases, including the read-only performance library, are not modified by ratings or collections. Asset identity uses the library root and main path, so renaming a library label or migrating database IDs retains picks; relocating a root requires remapping preferences.

Configure studio library roots and optional legacy path mappings in the studio configuration. Existing legacy databases are read through an adapter. New records use schema version 3 and relative media paths. On the first successful ingest into an old database, the worker writes `data.legacy.backup.json` before migrating the database. **Legacy browsers cannot read the new schema**; deploy TinyLib to all users before enabling ingest into the shared production library, or ingest into a separate library first.

## Ingest

Choose a writable destination, main category, asset name, tags, main color space and processing profile. Pick an individual image or detect a sequence from one frame. Explicit sequence syntax is `name.####.exr 1001-1100` or `name.%04d.exr 1001-1100`. Missing frames fail validation. A selected file remains a still unless sequence detection is requested. HDRI is an explicit asset type; highres is optional.

For each required representation, select supplied media or generation:

| Representation | Output | Dimensions |
| --- | --- | --- |
| Main | Original files copied unchanged | Original |
| Highres | Original files copied unchanged | Original |
| Proxy, footage | H.264 MP4, Rec.709 | 1920 × 1080 |
| Proxy, still/HDRI | JPEG, Rec.709 | 1920 × 1080 |
| Thumbnail | JPEG, Rec.709 | 960 × 506 |
| Filmstrip, footage only | JPEG, 24 tiles | 11520 × 270 |

Previews are fitted with black padding, preserving aspect ratio. Supplied previews must already be Rec.709; the worker checks dimensions, video frame count and FPS, but cannot infer whether untagged JPEG pixels have the correct color transform. Main/highres color is declared, not converted. Supply ACEScg EXRs for the studio's normal main format.

Generated thumbnail/filmstrip can use main or proxy. A proxy source is treated as already display-referred and is not passed through OCIO a second time. Short clips repeat sampled frames to make exactly 24 tiles. Largest-file thumbnail selection follows the old scripts; a middle-frame profile is also provided.

Set `OCIO` to an ACES configuration with the named color spaces. The public defaults locate conversion tools on PATH; set explicit executable paths in a local configuration when needed. The optional VFX Transcode profile invokes the existing executable with explicit OCIO, FPS, tool and output arguments. Check all paths on farm workers before deployment. Profile color names are OCIO names and must exist in the selected config.

“Review job” displays the exact processing manifest. “Save job manifest” exports it for inspection or local execution:

```powershell
python worker.py path\to\manifest.json
```

## Deadline 10.4 deployment

1. Deploy this package and `worker.py` to a farm-accessible shared folder.
2. Set `deadline.worker_script` and `deadline.spool_root` in the studio configuration to existing shared paths. They are deliberately empty in the workstation configuration until the studio deployment paths are chosen.
3. Ensure the Deadline Python plugin has the configured Python version (default 3.10). Set pool/group/priority as needed. Point each tool and OCIO config to paths available to workers.
4. Source media, destination libraries and the spool must be accessible to the worker account. Prefer UNC paths over mapped drives. This implementation targets a Windows farm; cross-platform path mapping is not implemented.
5. Use “Submit to Deadline”. One Python task copies the main/highres data, generates representations, validates them, and publishes the asset. The submitted job ID and manifest location are shown in the dialog.

Deadline Monitor reports processing output. Beside each manifest, `.submission.json` stores the Deadline ID and `.status.json` records processing/completion/failure. Refresh the browser after completion. Job polling is not built into the browser.

Workers stage files in `<library>/.tinylib-staging/<job-id>`. An exclusive library lock serializes publication; the database is reread under the lock and replaced atomically. Failed jobs retain staging for diagnosis and are not registered as assets. A completed manifest can be retried without duplication. Failed staging is not automatically removed: inspect it, then remove only that job's staging directory before retrying. A lock left after a terminated worker must be removed manually only after confirming no writer is active. On network storage, validate the server's exclusive-create and rename semantics before multi-worker rollout.

## Verification

```powershell
python -m unittest discover -s tests -v
python tests/qt_smoke.py
python tests/qt_features.py
python tests/qt_performance.py
python tests/processing_smoke.py
python tests/vfx_smoke.py
```

The real conversion tests use only sample inputs and write into unique folders under `artifacts`; they do not submit farm jobs. Screenshots are also saved there. Both PySide2 and Nuke 16's PySide6 runtime were checked, along with 15 core/filter/preference tests and real OIIO/FFmpeg and VFX Transcode conversions. The feature test exercises Ctrl-click selection, view switching, filters, star clicks, Qt drag/drop, collection CRUD and browser-restart persistence with temporary user preferences. The performance test reads the configured library and also uses temporary preferences. Live Nuke Read-node imports and a real Deadline job still require studio validation.

For standalone tests with Nuke 16's Python, set `PYTHONPATH` to its `pythonextensions/site-packages`, `QT_PLUGIN_PATH` to its `qtplugins` directory and `QT_QPA_PLATFORM_PLUGIN_PATH` to `qtplugins/platforms`. The smoke test sets `QT_QPA_PLATFORM=offscreen`. These overrides are for standalone testing only, not Nuke startup.

See `docs/processing-notes.md` for observations about the old scripts and utility integration.

## Local deployment settings

Public configurations contain portable examples. Create `config/studio.local.json`, `config/performance.local.json`, or `config/demo.local.json` for machine-specific settings. When present, these files take precedence over the corresponding configuration and are ignored by Git. Each local file is a complete configuration, not a partial overlay. Sample media, conversion binaries and generated artifacts are not distributed in this repository; provide them locally before running integration tests.
