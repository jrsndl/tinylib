# User guide

TinyLib identifies you by your Windows login; there is no password prompt. What you see and may change depends on the groups an administrator assigned to you (see [studio actions and access](actions-and-access.md)). Your account name is shown at the top of the browser.

Standalone TinyLib uses the studio-style dark Qt template in `tinylib/standalone.qss`. Inside Nuke it uses a Nuke-matched stylesheet. The standalone template uses Qt's built-in arrows, checks and navigation glyphs instead of bitmaps, so the controls stay readable without a resource bundle.

## Browser

The left panel is an expanded tree of libraries and their categories. Select a library to browse all its categories, a category to narrow that library, or "All libraries" to search across everything. **Refresh** rereads library data and permissions; open review players stay open. Restart the browser after the studio configuration changes.

Inside Nuke there is one browser per session: choosing **TinyLib > Studio library** again brings the existing window to the front.

During startup, the bottom status line reports the library being read, assets discovered, elapsed time, preview completion, local-cache hits, original-source reads, pending reads and unavailable previews. Hover over it to see library load errors, the local cache folder and the log file.

## Search

Search is case-insensitive across name, category, library and keywords. Multiple words must all match.

| Syntax | Meaning |
| --- | --- |
| `fire smoke` | Both words must match. |
| `"big burst"` | Phrase. |
| `tag:smoke` | Exact keyword. |
| `-night`, `-tag:night` | Exclude matches. |
| `!burst`, `!"big burst"` | Exclude a word or phrase. |
| `{key}:text` | Search any JSON asset field. |
| `{key}!text`, `-{key}:text` | Exclude a field value. |

Field searches combine with normal terms. `fire {acquire price}:usd` requires both the normal `fire` match and `usd` in `acquire_price`. Field names and values are case-insensitive, and spaces in labels match underscores in JSON keys. Combine several field filters, quote phrases (`{ingest note}:"slow motion"`), or use nested paths (`{metadata.width}:1920`). A leaf key such as `{width}:1920` also searches nested fields. Exclude a field value with `{ingest_note}!burst` or `{ingest note}!"big burst"`. A quote left open, as in `{note}:'slow`, searches the text as typed.

## Filters

The filtering panel combines:

- fulltext search, with an **Invert** checkbox that applies only to fulltext;
- comma-separated exact tags (all must match);
- media type;
- duration in seconds, pixel width and star count. Numeric filters offer `>`, `<` and `=`; select **Any** to disable a rule.

Duration is frame count divided by frame rate, including fractional rates. Stills have duration zero. Assets without duration or width metadata are excluded only while that numeric filter is enabled. Library, category, media type and keyword filters combine with search. **Clear filters** keeps the selected library or category.

## Views

- **Tiles** is the default, with thumbnails and hover filmstrips (24 frames). Still previews keep their aspect ratio. Card size is in the top control panel and applies to tiles.
- **Details** shows rows with a tiny preview and columns for name, library, category, media type, duration, dimensions, stars and keywords.
- **List** shows asset names in multiple columns when space permits.

Selection is kept when switching views. Use Ctrl-click, Shift-click or Ctrl+A for multi-selection. Import main/highres and star-rating actions apply to all selected assets; the properties panel summarizes a multi-selection.

**Play all / Stop all** animates the available filmstrips together in tiles and details. Only visible previews are decoded and painted, and assets scrolled into view join the shared animation position. List mode stays text-only. Stopping restores thumbnails; ordinary hover previews remain available.

### Tile text

**Info**, at the far right of the main view's top controls, shows or hides tile captions and the gray star row. With Info off, tiles contain only preview rectangles.

**Tile text…** sets the captions, up to six lines, using literal `\n` or real new lines. Tokens:

`{name}`, `{library}`, `{category}`, `{type}`, `{colorspace}`, `{tags}`, `{width}`, `{height}`, `{fps}`, `{length}` (seconds), `{first}`, `{last}`

Example: `{name}\n{width} × {height}`. The default is `{name}\n{type} · {width} × {height}`. Missing values show as `—`. Both settings are saved in your preferences; a studio `tile_template` provides the initial template.

## Paths

**Paths** chooses your file-sequence notation for external drag-and-drop, **Ctrl+C**, collection **Copy**, and the bundled Copy paths action. All output uses forward slashes.

| Notation | Example |
| --- | --- |
| Nuke (default) | `files.####.exr 1001-1100` |
| AYON | every frame path, joined with comma-space |
| Hashtag | `files.####.exr` |
| Houdini | `files.$F4.exr` (padding matches the sequence) |
| Flame | `files.[1001-1100].exr` |
| Printf | `files.%04d.exr` |
| Fallback | the main file's parent folder |

Stills remain ordinary file paths except in Fallback mode.

## Ratings

Star ratings are personal. The five stars in the top controls set the rating of the whole selection; **Clear** sets zero stars. Mixed ratings show five unfilled stars until you choose a value. With focus in the main view, press **1–5** to rate the selected assets or **0** to clear. The same keys work in the player for its asset.

## Collections

**Collections** opens a scratchpad between the main view and properties. It starts hidden every time.

- Create collections with **New** (collection01, collection02, …), rename or delete them. Deleting a collection never deletes media.
- Drag a main-view selection onto the collection list. With no collections, the first one is created automatically. Duplicate picks in one collection are ignored.
- Picked assets disappear from the main view, also while the panel is closed. Remove the pick or delete its collection to restore it; an asset stays hidden while another collection still contains it.
- Drag a collection selection back onto the main view to remove those references from the source collection, like **Remove selected from collection**. This works in all three views, including dropping onto empty space. Membership in other collections is kept.
- Collections are independent of the current search or category, with their own **Tiles / Details / List** selector and **Play all** toggle. Select items to preview or rate them. Unavailable assets stay listed with an unavailable label, so references are not lost silently.
- The collection action dropdown runs an action on the whole collection. A collection with unresolved references cannot run an action partially.
- **Copy** uses the selected Paths notation. A partial selection copies only those items; no selection or a complete selection copies the whole collection.
- **Export / Import** exchange simple JSON files; both file browsers start in Downloads. Imports create a new collection, adding a numeric suffix for duplicate names. Files contain references, not media; assets resolve through configured, permitted libraries with matching roots and main paths.

### Where personal data is stored

Ratings, collections and display settings are saved per user in `%APPDATA%/TinyLib/preferences.json` on Windows (`~/.config/TinyLib/preferences.json` without `APPDATA`). `TINYLIB_USER_PREFS` overrides the path. Saves are atomic and reread the file under a lock, so several browser windows don't overwrite each other. If the file is damaged (not valid JSON), TinyLib renames it to `preferences.damaged-<date>.json`, starts with empty preferences and tells you. If it is only temporarily unreadable, it is left untouched and reread before the next save.

Library databases are never changed by ratings or collections. Asset identity is the library root plus the main path, so renaming a library label or migrating database IDs keeps picks. Moving a library root requires remapping preferences.

## Player

Double-click an asset, double-click the properties thumbnail, or press **Enter** with exactly one selected asset to open the player in any view. Multiple selections do not open players.

The player decodes the proxy (or the thumbnail for stills) with FFmpeg and starts playing. The frame overlay shows the source frame number, starting at the asset's first frame. Decoding is asynchronous with a bounded frame cache, including backward playback. Previews are fitted within 960×540, keeping aspect ratio. Playback is silent. Slow storage or decoding can lower the achieved speed while buffering. A clip shorter than its metadata promises is trimmed to its real length.

| Control | Behavior |
| --- | --- |
| **J / L** | Play backward / forward. Repeated presses in the same direction step through 1×, 2×, 4×, 8× and 16×. Changing direction starts at 1×. |
| **K** | Stop at the current position and reset speed to 1×. |
| **Space** | Stop, or start forward playback at 1×. |
| **Left / Right** | Stop and step one frame backward / forward. |
| **O** | Toggle the frame-number overlay. |
| **0–5** | Set the asset's personal star rating; `0` clears it. |
| **Ctrl+C** | While stopped, copy the current proxy frame to the clipboard at the proxy's native resolution, without overlay or controls. |
| **Hover near the bottom** | Reveal the timeline and current time; click or drag its indicator to scrub. Scrubbing stops playback. |
| **Loop** | On by default for each player window. Wraps in both directions; when off, playback stops at either end and resets speed. |
| **Esc / Q** | Close the player. |

After a decoding error, seeking to another frame retries. A studio can switch to the external ffplay player instead (`player_backend`, see [configuration](configuration.md#browser-configuration)); it uses ffplay's own shortcuts and has none of the controls above.

## Actions and Nuke import

The properties action dropdown runs a studio action on the main-view selection; the collection dropdown runs it on the whole collection. Bundled actions import main or highres into Nuke and copy paths. Main and highres imports create Read nodes with explicit frame ranges and color space; an unknown project color space is reported instead of silently using a different one. Which actions you see depends on your groups and the host (see [studio actions and access](actions-and-access.md)).

## Asset properties

The right panel shows descriptive fields, not media file paths. Read-only identity fields come first: name, library, category, type and, for footage, **Range** such as `1001-1100`. Fields in the version, ingest and acquire groups are visible and editable only for groups the administrator allows.

### Edit

With an asset selected in a writable library, administrators and users with that library's edit permission can click **Edit** to change color space, keywords and metadata. The button turns into a red **Write** button.

- **Write** saves the draft and leaves edit mode. **Cancel edit** discards it.
- Selecting another asset saves the current draft automatically and keeps edit mode on. Moving within one library keeps the same lock; unchanged selection jumps write nothing. Moving to another library switches to that library's lock.
- If validation or writing fails, TinyLib returns to the edited selection and keeps the draft so you can correct or cancel it.
- If edit mode cannot start (for example, someone else holds the lock), TinyLib leaves edit mode after one message.

While you edit, other users see who holds the library and cannot edit it or publish an ingest into it. See [edit locks](admin.md#edit-locks) for details. Read-only libraries cannot be edited, even by administrators.

### Multiedit

Select several assets of the same type and click **Edit**. Editable text fields have orange borders; changed fields turn red.

- When the selected assets differ in an editable text field, the field starts empty and shows the values as grey placeholder text (`Multiple values: a | b`). Typing replaces the value on every asset.
- Keywords show the union of all keywords.
- Only red fields are written, to every selected asset, when the selection changes or **Write** is clicked.
- Numeric, checkbox and structured metadata fields are read-only in multiedit.

Library edit locks are kept while switching selections. An invalid draft returns you to the original selection for correction.

## Ingest

**+ Ingest asset** opens the ingest form if you may ingest into at least one writable library. The form is not modal: the browser and the submission progress windows stay usable while it is open, and reopening it brings the existing form forward.

1. Choose a writable destination library and an asset type. Footage is the default. TinyLib supports Still, Footage, Model, Folder, Splat, PDF and Material. A 360 image or clip is a Still or Footage with `is_equirectangular` enabled. See [asset types and metadata](asset-types.md) for each type's representations and fields.
2. Choose an existing main category, or enable **Create new** and type a name to publish into a new top-level category folder.
3. Choose or drop the main source. The form accepts files and folders dropped from the OS. For Footage, choosing one frame of a sequence detects the contiguous sequence and writes it as `name.####.exr 1001-1100`; choosing a folder switches to [folder batch](#footage-folder-ingest). Missing frames and extensions outside the selected type are rejected.
4. TinyLib fills the asset name (the file name without extension, sequence counter and `v###` token) and lowercase keywords (split at separators and CamelCase). It never overwrites a name or keywords you typed.
5. Set the main color space, the frame rate for footage, and optionally a highres source where the type supports it.
6. For each representation, supply a file or let the farm generate it.

| Representation | Output | Dimensions |
| --- | --- | --- |
| Main | Original files copied unchanged | Original |
| Highres | Original files copied unchanged | Original |
| Proxy, footage | H.264 MP4, Rec.709 | 1920 × 1080 |
| Proxy, still | JPEG, Rec.709 | 1920 × 1080 |
| Thumbnail | JPEG, Rec.709 | 960 × 506 |
| Filmstrip, footage only | JPEG, 24 tiles | 11520 × 270 |

Generated previews are available for Still and Footage; other types need a supplied JPEG thumbnail. Previews are fitted with black padding, keeping aspect ratio. Supplied previews must already be Rec.709: the worker checks dimensions, video frame count and frame rate, but cannot tell whether an untagged JPEG has the right color transform. Main and highres color is declared, not converted; supply ACEScg EXRs for the studio's usual main format.

The generated thumbnail and filmstrip can come from main or from the proxy. A proxy source is already display-referred and is not passed through OCIO again. Short clips repeat sampled frames to make exactly 24 tiles. The processing profile decides the thumbnail frame: the largest file (an approximation of the most active frame, as in the old scripts) or the middle frame.

**Review job** shows the exact processing manifest. **Save job manifest** exports it for inspection or local execution with `python worker.py path\to\manifest.json`. **Submit to Deadline** sends one job; its ID and manifest location are shown when Deadline confirms. Manifests are prepared in the background, so the form stays responsive while network folders are read. Refresh the browser after the job completes; the browser does not poll Deadline.

### Footage folder ingest

With the Footage type, choosing a folder as Main switches the form to folder batch, which submits the whole folder. Choose the destination library, main category, source folder, main color space, processing profile, and whether previews come from main or proxy.

Submission recursively finds every image sequence and container and creates a separate Deadline job for each. Asset names and keywords come from file names; frame rate and type metadata come from probing each source. Proxy, thumbnail and filmstrip are always generated, and highres is skipped.

All manifests are prepared before any job is submitted. A missing frame rate, duplicate automatic names, invalid sources or existing destination assets are reported before anything is submitted. The background progress window lists each confirmed Job ID and keeps those IDs if a later submission fails. The form stays reusable during submission.
