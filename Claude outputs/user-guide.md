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

- **Search**: all terms must match; put `!` before a term to exclude it (`!burst`, `!"big burst"`, `{tags}!fire`);
- **Pick tag…**, which adds `{tags}:<tag>` to the search (several picked tags must all match);
- media type;
- duration in seconds and pixel width. These numeric filters offer `>`, `<` and `=`; select **Any** to disable a rule. Typing a number while the operator is **Any** turns the rule on with `>`. Clearing the number turns it off again. Length takes decimals (a point or a comma);
- star rating, with the same five stars as **Rate selection**: click a star to show assets with at least that many stars. Click the highest lit star again, or **Any**, to show every rating. To find unrated assets, search `{_rating}:0`.

Duration is frame count divided by frame rate, including fractional rates. Stills have duration zero. Assets without duration or width metadata are excluded only while that numeric filter is enabled. Library, category, media type and keyword filters combine with search. **Clear filters** keeps the selected library or category.

## Views

- **Tiles** is the default, with thumbnails and hover filmstrips (24 frames). Still previews keep their aspect ratio. Card size is in the top control panel and applies to tiles.
- **Details** shows rows with a tiny preview and columns for name, library, category, media type, duration, dimensions, stars and keywords.
- **List** shows asset names in multiple columns when space permits.

Selection is kept when switching views. Use Ctrl-click, Shift-click or Ctrl+A for multi-selection. Import main/highres and star-rating actions apply to all selected assets; the properties panel summarizes a multi-selection.

**Play all / Stop all** animates the available filmstrips together in tiles and details. Only visible previews are decoded and painted, and assets scrolled into view join the shared animation position. List mode stays text-only. Stopping restores thumbnails; ordinary hover previews remain available.

### Tile text

**Info**, at the far right of the main view's top controls, shows or hides tile captions. With Info off, tiles contain only preview rectangles. A rated asset shows its stars in the top right corner of its preview, with Info on or off; a lat-long still shows a **360** badge in the top left corner.

**Tile text…** sets the captions, up to six lines, using literal `\n` or real new lines. Tokens:

`{name}` (the label, if the asset has one), `{ingest_name}` (always the name used at ingest), `{library}`, `{category}`, `{type}`, `{colorspace}`, `{tags}`, `{width}`, `{height}`, `{fps}`, `{length}` (seconds), `{first}`, `{last}`

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

**Collections** opens a scratchpad between the main view and properties. It starts hidden every time. **Properties**, to its right, shows or hides the asset properties panel; it is on by default, TinyLib remembers your choice, and **Edit** shows the panel again.

- Create collections with **New** (collection01, collection02, …), rename or delete them. Deleting a collection never deletes media.
- Drag a main-view selection onto the collection list. With no collections, the first one is created automatically. Duplicate picks in one collection are ignored.
- Picked assets disappear from the main view, also while the panel is closed. Remove the pick or delete its collection to restore it; an asset stays hidden while another collection still contains it.
- Drag a collection selection back onto the main view to remove those references from the source collection, like **Remove selected from collection**. This works in all three views, including dropping onto empty space. Membership in other collections is kept.
- Collections are independent of the current search or category, with their own **Tiles / Details / List** selector and **Play all** toggle. Select items to preview or rate them. Unavailable assets stay listed with an unavailable label, so references are not lost silently.
- The collection action dropdown runs an action on the whole collection. A collection with unresolved references cannot run an action partially.
- **Copy** uses the selected Paths notation. A partial selection copies only those items; no selection or a complete selection copies the whole collection.
- **Export / Import** exchange simple JSON files, including each asset's in and out points; both file browsers start in Downloads. Imports create a new collection, adding a numeric suffix for duplicate names. Files contain references, not media; assets resolve through configured, permitted libraries with matching roots and main paths.

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
| **I / O** | Footage: set the in / out point at the current frame. An in point after the out point moves the out point to the end, and the reverse. |
| **Alt+X** | Clear the in and out points (the whole clip plays again). |
| **F** | Toggle the frame-number overlay. |
| **0–5** | Set the asset's personal star rating; `0` clears it. |
| **PgUp / PgDn** | Previous / next asset, in the same window: the next asset replaces this one. Opened from the main view, the player steps through the assets the main view shows now, filtered and sorted as it is; opened from the current collection, through that collection. Opened from the Properties panel, it steps through the list the shown asset was selected in: the main view, or the collection when the asset was selected there. Both ends wrap: PgDn on the last asset opens the first, PgUp on the first the last. The next asset opens the way you left the last one: playing (same direction and speed) or stopped, with Loop and the frame overlay as they were; in the 360 viewer flat or sphere, proxy or HDR (main or highres), with the same exposure and gamma. A lat-long still opens in the 360 viewer and other assets in the review player, so the window type can change on the way. |
| **Ctrl+C** | While stopped, copy the current proxy frame to the clipboard at the proxy's native resolution, without overlay or controls. |
| **Hover near the bottom** | Reveal the timeline and current time; click or drag its indicator to scrub. Scrubbing stops playback. |
| **Loop** | On by default for each player window. Wraps in both directions between the in and out points (the whole clip when none are set); when off, playback stops at the in or out point and resets speed. |
| **Esc / Q** | Close the player. |

### In and out points

In and out points trim footage for review. They are personal, like star ratings: each artist has their own, they are kept in your preferences file, and the library database never changes. The timeline shows them as a blue band with end marks, and the time line reads `In 1012 · Out 1080`. The player opens at the in point and plays only from in to out.

Copied and dragged paths use the in and out points as the frame range in the **Nuke**, **AYON** and **Flame** notations (for example `shot.####.exr 1012-1080`); the other notations carry no range. Copying from a collection uses the collection's points.

A player opened from a collection uses that collection's in and out points instead of yours, and **I**, **O** and **Alt+X** change the collection's. Adding an asset to a collection copies your current points into it. Exported collections carry their points, so importing a colleague's collection shares them without changing your own.

After a decoding error, seeking to another frame retries. A studio can switch to the external ffplay player instead (`player_backend`, see [configuration](configuration.md#browser-configuration)); it uses ffplay's own shortcuts and has none of the controls above.

### 360 viewer

A lat-long Still (an HDRI, `is_equirectangular` enabled; its tile shows a **360** badge) opens in the 360 viewer instead, with either player backend. You look around from inside the sphere. It opens on the proxy, which is a tonemapped JPEG and is shown as it is. The **Proxy / Main (HDR)** toggle button decodes the EXR or HDR main (or the highres, see below) in the background and shows it scene-linear: exposure (gain, in stops) and gamma apply, then the sRGB curve, as in Nuke's viewer. **The main and highres views are not color accurate.** They are for checking dynamic range, exposure and detail, not color. The image is not converted from its color space through OCIO and no display transform or look is applied: the linear values go straight through the sRGB curve. An ACEScg main therefore looks more saturated than its proxy, and highlights clip instead of rolling off. Judge color on the proxy, or in Nuke. A main wider than 8192 pixels is shown downsized by a whole number (configurable with `pano_max_width`). The information line at the bottom right shows what is displayed and its size (with the file's own size when it was downsized), the brightest value, which tool read it, and the field of view or flat zoom. The bottom-left readout shows the pixel under the cursor: linear values on the main, 8-bit display values on the proxy.

| Control | Behavior |
| --- | --- |
| **Drag** | Look around. Looking up and down stops at the poles. |
| **Wheel** | Field of view, 10° to 150° (90° at start). |
| **Double-click / R** | Back to straight ahead at 90° (in the flat view: fit the whole image). |
| **Tab**, **P**, **M** | Toggle proxy/main, as the toggle button does; proxy; main. The view direction is kept. |
| **Use highres if available** | Off by default. On, the HDR image is the asset's highres instead of its main (downsized above `pano_max_width` like the main). Unavailable when the asset has no highres. |
| **F** | Toggle the flat lat-long view. In it, drag pans and the wheel zooms (up to 64×) about the mouse; **double-click** or **R** fits the whole image again. |
| **Exposure**, **Gamma** sliders | Shown only for the HDR image; the proxy is already tonemapped. Exposure ±10 stops, gamma 0.25–4, both neutral in the middle. Click the **Exposure** or **Gamma** caption to put that slider back in the middle. |
| **+ / −**, **G** | Exposure ±½ stop; reset exposure and gamma. |
| **0–5** | Set the asset's personal star rating. |
| **PgUp / PgDn** | Previous / next asset, as in the review player. |
| **Esc / Q** | Close the viewer. |

The viewer needs OpenGL 3.0. Decoding the main needs numpy, which Nuke and the standalone TinyLib.exe include. The main is read by FFmpeg in its own pixel format, never converted, so values above 1.0 are kept; a main FFmpeg cannot read that way is read by oiiotool.

## Actions and Nuke import

The properties action dropdown runs a studio action on the main-view selection or, with nothing selected, on every asset the main view shows (filtered as it is); its first entry then reads **Pick action for all N shown…**, and running it on more than one asset asks first. The collection dropdown runs an action on the whole collection. An action is listed only when it suits every asset it would run on (its asset filter) and the host. Bundled actions import main or highres into Nuke and copy paths. Main and highres imports create Read nodes with explicit frame ranges and color space; an unknown project color space is reported instead of silently using a different one. Which actions you see depends on your groups and the host (see [studio actions and access](actions-and-access.md)).

## Asset properties

The right panel shows descriptive fields, not media file paths. Read-only identity fields come first: name, library, category, type, **Parts** and, for footage, **Range** such as `1001-1100`. **Parts** lists the asset's components with their file types, main first and the others alphabetically, for example `main:exr proxy:mp4` for footage or `main:exr highres:exr proxy:jpg` for an HDRI; thumbnail and filmstrip are not listed, and a Folder asset's components show `folder`. For a multi-selection with different parts, the variants are separated by `|`. Fields in the version, ingest and acquire groups are visible and editable only for groups the administrator allows.

### Edit

With an asset selected in a writable library, administrators and users with that library's edit permission can click **Edit** to change color space, keywords and metadata. The button turns into a red **Write** button.

- **Write** saves the draft and leaves edit mode. **Cancel edit**, which appears beside **Write** while editing, discards it.
- The heading above the fields becomes the **label**, prefilled with the name. Type a new label to rename the asset in the browser: tiles, the details view, the panel heading, players and Nuke Read nodes show the label instead of the name. The **Name** field keeps the name used at ingest, which also names the asset's folder, so nothing on disk is renamed. Set the label back to the name, or clear it, to show the name again. Search finds assets by label and by name. Labels are edited one asset at a time, never in multiedit.
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

### Delete

Deleting is for administrators only; other users don't see the button. While editing, select one or more assets and click **Delete**, left of **Cancel edit**. The assets are only marked: their previews get a red veil and their rows turn red and struck through. Marks stay while you select other assets; click **Undo delete** with marked assets selected to unmark them. **Cancel edit** drops all marks.

**Write** then opens a confirmation window that lists the marked assets like the details view, each with a checked **Delete** box. Uncheck an asset to keep it. Choose:

- **Delete from database only** (the default): the assets leave the library; their files stay where they are.
- **Delete from storage and database**: also deletes each asset's folder, `<library>/<category>/<name>`. This cannot be undone. TinyLib never deletes a folder that is not exactly that, that holds another asset, or anything outside it; such files are kept and listed after the deletion.

**Cancel** returns to edit mode with the marks kept. Deleting needs administrator rights and edit permission on the asset's library (both checked again when you confirm), and takes that library's edit lock if it is not already held. If an asset changed in the database since you loaded it, nothing in that library is deleted and TinyLib asks you to refresh. A library without `tinylib_data.json` lists its folders on disk and has no database to delete from; move or delete its folders instead. TinyLib never follows a link or junction when deleting files. Collections keep a deleted asset as unavailable.

## Ingest

**+ Ingest asset** opens the ingest form if you may ingest into at least one writable library. The form is not modal: the browser and the submission progress windows stay usable while it is open, and reopening it brings the existing form forward.

1. Choose a writable destination library and an asset type. Footage is the default. TinyLib supports Still, Footage, Model, Folder, Splat, PDF and Material. A 360 image or clip is a Still or Footage with `is_equirectangular` enabled; for an HDRI see [lat-long stills](#lat-long-stills-hdri). See [asset types and metadata](asset-types.md) for each type's representations and fields.
2. Choose the main category:
   - **Pick existing**: one of the library's categories.
   - **Create new**: type a name to publish into a new top-level category folder.
   - **From main folder**: the name of the folder 1, 2 or 3 levels above the main file. For `c:\ingest\fire\mains\assetA.####.exr`, 1 level up is `mains` and 2 levels up is `fire`. The form shows the result.
3. Choose or drop the main source. The form accepts files and folders dropped from the OS. For Footage, choosing one frame of a sequence detects the contiguous sequence and writes it as `name.####.exr 1001-1100`; for Footage or Still, choosing a folder switches to [folder batch](#folder-ingest). Missing frames and extensions outside the selected type are rejected.
4. TinyLib fills the asset name (the file name without extension, sequence counter and `v###` token) and lowercase keywords (split at separators and CamelCase). It never overwrites a name or keywords you typed.
5. Set the main color space, the frame rate for footage, and optionally a highres source where the type supports it.
6. For each representation, supply a file or let the farm generate it.

| Representation | Output | Dimensions |
| --- | --- | --- |
| Main | Original files copied unchanged | Original |
| Highres | Original files copied unchanged | Original |
| Proxy, footage | H.264 MP4, Rec.709 | 1920 × 1080 |
| Proxy, still | JPEG, Rec.709 | 1920 × 1080 when generated; a supplied still proxy may be any size |
| Proxy, lat-long still | JPEG, Rec.709, 2:1 | 4096 × 2048 when generated; supplied: any 2:1 size |
| Thumbnail | JPEG, Rec.709 | 960 × 506 |
| Thumbnail, lat-long still | JPEG, Rec.709, 2:1 | 960 × 480 when generated; supplied: any 2:1 size |
| Filmstrip, footage only | JPEG, 24 tiles | 11520 × 270 |

Generated previews are available for Still and Footage; other types need a supplied JPEG thumbnail. Previews are fitted with black padding, keeping aspect ratio. Supplied previews must already be Rec.709: the worker checks dimensions, video frame count and frame rate, but cannot tell whether an untagged JPEG has the right color transform. Main and highres color is declared, not converted; supply ACEScg EXRs for the studio's usual main format.

For Footage, what the farm does depends on what you supply:

| You supply | The farm |
| --- | --- |
| Main only, EXR | Makes proxy, thumbnail and filmstrip in one vfx-transcode run; each frame is converted once. |
| Main and proxy | Takes the generated thumbnail and filmstrip from the proxy with FFmpeg. The proxy is already display-referred and is not passed through OCIO again. vfx-transcode is not used. |
| Main only, display-referred (a movie such as MOV or MP4, or a PNG, TIFF, DPX or JPG sequence) | Converts the main to scene-linear EXRs in the worker's temp folder (a movie via 16-bit PNGs), using the main color space you chose, then continues as for an EXR main. |

The highres is never converted. The main is never changed either; the scene-linear EXRs are only used to make the previews. Short clips repeat sampled frames to make exactly 24 tiles. The processing profile decides the thumbnail frame: the busiest frame (the largest converted file) or the middle frame.

### Lat-long stills (HDRI)

An HDRI is ingested ready-made, as a Still with **Projection: Lat-long (equirectangular HDRI)** ticked, which sets `is_equirectangular` in the type metadata. Choosing a 2:1 EXR or HDR main ticks it automatically; untick it for a 2:1 image that is not a panorama. A lat-long main must be EXR or HDR. The highres (for example a 16K version) is optional and copied as it is. The proxy and thumbnail are tonemapped 2:1 lat-long JPEGs: supply your own at any 2:1 size, or let the farm make them from the main through the profile's color conversion (4096 × 2048 and 960 × 480; a profile can set `latlong_proxy_size`). With a supplied proxy, a generated thumbnail is taken from the proxy. The worker rejects a proxy or thumbnail that is not 2:1, and warns when the main itself is not 2:1.

**Review job** shows the exact processing manifest. **Save job manifest** exports it for inspection or local execution with `python worker.py path\to\manifest.json`. **Submit to Deadline** sends one job; its ID and manifest location are shown when Deadline confirms. Manifests are prepared in the background, so the form stays responsive while network folders are read. Refresh the browser after the job completes; the browser does not poll Deadline.

### Folder ingest

With the Footage or Still type, choosing a folder as Main switches the form to folder batch, which submits the whole folder. Choose the destination library, main category, source folder, main color space and processing profile. **From main folder** sets each asset's category from its own main file.

Submission recursively finds the mains and creates a separate Deadline job for each:

- **Footage**: every image sequence and container. Frame rate and type metadata come from probing each source.
- **Still**: every image file of a Still extension, each its own asset; numbered files such as `sky_01.exr` and `sky_02.exr` are not joined into a sequence. Size comes from probing each file with oiiotool (the worker measures it again). Tick **Projection: Lat-long** to flag **every** still of the batch as a lat-long HDRI; then only EXR and HDR files are mains, so JPEG previews beside them are never ingested as assets of their own.

Asset names and keywords come from file names.

Proxies, highres, thumbnails and (for footage) filmstrips that already exist are paired with their mains. Choose where they are in **Components are in**:

- **Separate folder structure**: each component has its own folder, for example `c:\ingest\fire\mains`, `c:\ingest\fire\proxies` and `c:\ingest\fire\highres`. Choose `mains` as Main and the other folders in the Proxy, Highres, Thumb and Filmstrip rows. A component folder may also sit inside the main folder; its files are never taken as mains.
- **Same folder structure**: the components are among the mains. Enter a regular expression per component. It is matched against each file's name and against its path relative to the main folder, with forward slashes (a sequence by its first frame): for example `^.*\.mp4$` for proxies and `^.*_8K.*\.exr$` for highres, or `/proxy/` for files in any `proxy` subfolder. Files a component regex matches are not ingested as mains.

For HDRIs laid out one folder per asset, like `hdri\bakery\main\bakery.exr`, `hdri\bakery\proxy\bakery.jpg`, `hdri\bakery\thumb\bakery.jpg` and `hdri\bakery\highres\bakery.exr`: choose Still, tick Lat-long, choose `hdri` as Main, pick **Same folder structure** and enter `/proxy/`, `/highres/` and `/thumb/`.

The Proxy, Thumb and Filmstrip rows choose how that preview is made: **Paired, else generate** (the default) uses the paired file and generates the preview for a main without one; **Supply (paired)** requires a paired file for every main and stops the batch with a list of the mains that have none; **Generate** always generates it from the main, ignoring paired files (files the row's folder or regex finds are still never ingested as mains). The component folders take folders only. Leave a row empty to leave that component unpaired. A component pairs with the main whose clean file name (no folder, extension, frame counter or the separator before it) it equals or starts with: `assetA.1001.exr` pairs with `proxies/assetA.mp4` and `highres/assetA_8K.1001.exr`. If several mains match, the longest name wins. A component that matches no main, or a main with two candidates for the same component, stops the batch with a list of the files. Use **Review job** to check the pairing before submitting.

Each asset then follows the tables above: with a paired proxy, the thumbnail (and filmstrip) come from it; without one, everything is generated from the main. Paired highres, thumbnails and filmstrips are used as they are.

All manifests are prepared before any job is submitted. A missing frame rate (footage), duplicate automatic names, invalid sources or existing destination assets are reported before anything is submitted. The background progress window lists each confirmed Job ID and keeps those IDs if a later submission fails. The form stays reusable during submission.
