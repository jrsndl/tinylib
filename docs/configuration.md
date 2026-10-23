# Configuration reference

TinyLib reads three kinds of configuration:

| File | Chosen by | Holds |
| --- | --- | --- |
| [`tinylib.ini`](#tinylibini) | Its location: the app folder, beside `menu.py` and `worker.py` | Paths of the security file and the default browser configuration; the executables TinyLib runs |
| [Browser configuration](#browser-configuration) (`studio.json`) | `--config`, `TINYLIB_CONFIG`, `tinylib.ini` or the built-in default | Libraries, processing profiles, Deadline, actions, display defaults |
| [Security file](#security-file) (`security.json`) | `tinylib.ini` only | Users, groups, grants and the libraries they apply to |

The browser configuration and the security file may be the same file; the public `config/studio.json` contains an `access` block so it can serve as both for a local trial. Personal settings (ratings, collections, display choices) live in [user preferences](#environment-variables), which never grant access.

## tinylib.ini

```ini
[tinylib]
security = //studio/pipeline/tinylib/security.json
config   = //studio/pipeline/tinylib/studio.json

[tools]
ffmpeg        = C:/tools/ffmpeg/ffmpeg.exe
ffprobe       = C:/tools/ffmpeg/ffprobe.exe
oiiotool      = C:/tools/oiio/oiiotool.exe
vfx_transcode = C:/tools/vfx-transcode/vfx-transcode.exe
ocio          = //studio/pipeline/ocio/aces_1.2/config.ocio
```

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `security` | path | none (required) | The studio [security file](#security-file). |
| `config` | path | none | Default browser configuration, used when neither `--config` nor `TINYLIB_CONFIG` is given. |

- The file must have a `[tinylib]` section. If the file is missing, unreadable, or `security` is empty, nobody gets access and the status line shows why.
- Relative paths resolve beside `tinylib.ini`. Paths starting with `//` or `\\` are treated as absolute. Surrounding double quotes are removed.
- Environment variables and `%(name)s` interpolation are **not** expanded.
- Nothing else can choose the security file: not `TINYLIB_CONFIG`, `--config`, a `.local.json` override, or the old `security_config` key.
- `[tools]` is optional and takes the keys listed under [tools](#tools). A bare name (`ffmpeg`) is looked up on PATH; a relative path (`executables/ffmpeg/ffmpeg.exe`) resolves beside `tinylib.ini`. An empty or missing line falls back to the browser configuration, then to the bare name.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `TINYLIB_CONFIG` | Browser configuration to use when no explicit path is given. Overrides `config` in `tinylib.ini`. Never affects the security file. |
| `TINYLIB_USER_PREFS` | Path of the personal preferences file. Default `%APPDATA%/TinyLib/preferences.json`, or `~/.config/TinyLib/preferences.json` when `APPDATA` is not set. |
| `NUKE_TEMP_DIR` | Preferred parent of the local cache and log folder (`<NUKE_TEMP_DIR>/tinylib`). See [local thumbnail cache](deployment.md#local-thumbnail-cache). |
| `OCIO` | Not read by TinyLib directly; the public profiles use `"${OCIO}"` as their `ocio_config`. A farm worker without it needs `ocio` under `[tools]` in `tinylib.ini`. |

## Browser configuration

### Which file is loaded

The first of: the path given to `python launch.py --config` or `tinylib.show(path)`; `TINYLIB_CONFIG`; `config` in `tinylib.ini`; `config/studio.json` in the app folder. `python launch.py --demo` uses `config/demo.json`, which is not distributed.

If a sibling file with `.local` before the extension exists (for example `studio.local.json` beside `studio.json`), it is loaded **instead**. It must be a complete configuration; nothing is merged. The original file does not need to exist.

### Variables and relative paths

- Every string value in the browser configuration is passed through environment-variable expansion when loaded: `$VAR` and `${VAR}`, and on Windows also `%VAR%`. Unset variables are left as written. This does not apply to `tinylib.ini`.
- Relative paths resolve beside the browser configuration file that was loaded (the `.local.json` file when one is used):

| Value | `{app}/…` | `./…` or `../…` | Other relative | Absolute or UNC |
| --- | --- | --- | --- | --- |
| `libraries[].root` | not supported | beside the configuration | beside the configuration | as written |
| `tools.*` | inside the install folder | beside the configuration | as written: a bare name such as `ffmpeg` is found on PATH | as written |
| `action_roots[]` | inside the install folder | beside the configuration | beside the configuration | as written |

`{app}` is the TinyLib install folder, the one containing `menu.py` and the `tinylib` package. Write it at the start of the value: `"{app}/actions"`, `"{app}/executables/ffmpeg/ffmpeg.exe"`. Other path values (`ocio_config`, Deadline paths) are used as written; an environment variable that is not set stays as written, e.g. `${OCIO}`.

### Validation

Loading stops with an error naming the file and key when:

- the top level is not a JSON object;
- `libraries` is not a list, an entry is not an object, or an entry lacks a non-empty `name` or `root`;
- `tools`, `profiles` or `deadline` is not an object, or a `tools` value is not a string;
- `action_roots` is not a list of strings.

Loading also fails when `extension_groups` is not an object, names an unknown group, or a group is not a list of strings.

### Top-level keys

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| [`libraries`](#libraries) | list of objects | `[]` | Libraries shown in the browser. |
| `colorspaces` | list of strings | `["ACES - ACEScg"]` | Main color space choices in the ingest form. Use OCIO names from the profiles' OCIO config. |
| `transcode_main` | object | `{"extension": "exr", "compression": "dwaa", "start_frame": 1001, "output_colorspace": "ACEScg"}` | Defaults of the ingest form's [Transcode main](user-guide.md#transcode-main) fields; any key can be left out. |
| [`extension_groups`](#extension_groups) | object | built-in groups | Override the file extensions of the predefined groups. |
| [`tools`](#tools) | object of paths | `{}` | External executables. Prefer `[tools]` in `tinylib.ini`, which overrides this. |
| [`profiles`](#profiles) | object | `{}` | Processing profiles offered at ingest. |
| [`deadline`](#deadline) | object | `{}` | Deadline submission. |
| `action_roots` | list of folder paths | `["{app}/actions"]` | Folders of action packages; see [action packages](actions-and-access.md#action-packages). |
| `tile_template` | string | `{name}\n{width} × {height} {length} s` | Initial tile caption template. A user's own **Tile text…** setting wins. An invalid template falls back to the default. Tokens are listed in the [user guide](user-guide.md#tile-text). |
| `player_backend` | `"interactive"` or `"ffplay"` | `"interactive"` | `"ffplay"` launches the external ffplay player instead of the built-in review player. Any other value means interactive. |
| `pano_max_width` | integer | `8192` | Widest main or highres the [360 viewer](user-guide.md#360-viewer) shows unreduced. A wider one is downsized by a whole number (16384 → 8192). Each 8192 × 4096 main uses about 130 MB of graphics memory. |
| `library_defaults` | object | `{}` | Starting entry for **Create new library…** with "Studio defaults", for example `{"read_only": false, "legacy_roots": []}`. Copied as written; name and root come from the dialog. |
| `access` | object | | Ignored, unless this file is also the security file. |
| `security_config` | | | Ignored. The security file is chosen only by `tinylib.ini`. |

TinyLib adds keys starting with `_`, plus `asset_type_schema_version`, when it loads the file. Do not set them.

### libraries

```json
"libraries": [
  {"name": "Elements", "root": "//studio/library/elements", "legacy_roots": ["D:/old_share/elements"]},
  {"name": "Reference", "root": "//studio/library/reference", "read_only": true}
]
```

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | required | Display name. Must be unique for **Create new library…**. |
| `root` | path | required | Folder containing `tinylib_data.json` and the `category/asset` folders. |
| `read_only` | boolean | `false` | Excludes the library from ingest destinations and editing, for everyone. `read_only` in the security file has the same effect; either one is enough. |
| `legacy_roots` | list of paths | `[]` | Absolute root prefixes stored in an old database. A stored path starting with one of them (case-insensitive, `\` or `/`) is remapped onto `root`. |
| `permissions` | object | | Not used for access in a browser configuration; only the security file's `permissions` count. |

A library appears in the browser only if it is listed here, and grants non-admins anything only if the security file also lists it. The two entries are matched by their resolved root folder.

### extension_groups

Overrides the extension lists of the eight predefined groups: `extensions_stills`, `extensions_stills_raw`, `extensions_footage_containers`, `extensions_models`, `extensions_3dscene`, `extensions_splats`, `extensions_materials`, `extensions_workfiles`. A group given here replaces that group's built-in list; groups not given keep their defaults. Values are lowercased and a leading dot is removed. Other group names are rejected. The defaults are in `tinylib/asset_types.py` and the public `config/studio.json`; their use is described in [asset types](asset-types.md). The groups are copied into each ingest manifest, and the worker uses the manifest's copy.

### tools

Set tools in the `[tools]` section of [`tinylib.ini`](#tinylibini). Because that file is only writable by administrators, it decides which programs TinyLib runs on workstations and on the farm. A `tools` object in the browser configuration is still read for compatibility, but every tool set in `tinylib.ini` overrides it.

| Key | Default | Used for |
| --- | --- | --- |
| `ffmpeg` | `ffmpeg` | Generating proxies, thumbnails and filmstrips; decoding in the review player; reading HDR mains in the 360 viewer. |
| `ffprobe` | `ffprobe` | Probing containers (ingest, Rescan metadata) and checking generated media. The player uses `ffprobe` beside `ffmpeg` when this key is not set. |
| `oiiotool` | `oiiotool` | Color conversion of image files; probing images and sequences; reading a main the 360 viewer's FFmpeg cannot read without converting it. |
| `vfx_transcode` | `vfx-transcode` | Proxies, thumbnails and filmstrips of EXR image sequences; see `proxy_backend` under [profiles](#profiles). When it is not found on a worker, that worker uses the built-in path. Needs version 2026.09 or newer. |
| `ffplay` | `ffplay` beside `ffmpeg`, else `ffplay` on PATH | Only with `"player_backend": "ffplay"`. |
| `ayon_console` | beside `AYON_EXECUTABLE` | The AYON launcher console (`ayon_console.exe`) that runs [Publish to AYON](ayon-publish.md). Needed outside a host started from AYON. |
| any other name, e.g. `rv`, `djv`, `mrv2` | that name on PATH | Programs that [actions](actions-and-access.md#external-tools) run. **Open in Player** offers every tool except TinyLib's own (`ffmpeg`, `ffprobe`, `ffplay`, `oiiotool`, `vfx_transcode`, `ocio`, `ayon_console`). |
| `ocio` | the profile's `ocio_config` | OCIO config file for every processing profile on this machine; overrides `ocio_config`. A path, or a built-in config such as `ocio://studio-config-latest` (OCIO 2.2+). Set it on farm workers that have no `OCIO` environment variable. |

The farm worker uses the tools from **its own** `tinylib.ini` (and configuration), never from the job manifest. External tools time out: probes after 60 seconds, each conversion step after one hour.

### profiles

The ingest form lists profiles by name. The worker looks the same name up in its own configuration and fails the job if the profile is missing there and anything is to be generated.

```json
"profiles": {
  "ACES to Rec.709": {
    "ocio_config": "${OCIO}",
    "output_colorspace": "Output - Rec.709",
    "color_args": ["--colorconvert", "{input_colorspace}", "{output_colorspace}"],
    "thumbnail_frame": "largest"
  }
}
```

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `ocio_config` | path | none | OCIO config file. `ocio` under `[tools]` in `tinylib.ini` overrides it. Must exist on the worker whenever previews are generated from image files (stills and image sequences), and for vfx-transcode, which also accepts a built-in config such as `ocio://studio-config-latest`. Footage containers are converted with FFmpeg and do not use it. |
| `output_colorspace` | string | `Output - Rec.709` | OCIO display color space of generated previews. |
| `color_args` | list of strings | `["--colorconvert", "{input_colorspace}", "{output_colorspace}"]` | oiiotool arguments for the color conversion. `{input_colorspace}` is the main color space chosen at ingest; `{output_colorspace}` is `output_colorspace`. An empty list is an error. |
| `thumbnail_frame` | `"largest"` or `"middle"` | `"largest"` | Frame used for a generated thumbnail: the largest source file, or the middle frame. |
| `proxy_backend` | `"vfx_transcode"` or `"builtin"` | automatic | How the previews of EXR image sequences are made. vfx-transcode makes the proxy, filmstrip and thumbnail in one `tools.vfx_transcode` run: the frames are converted once, in the worker's local temp folder, at the size the proxy shows. With `preview_source` `proxy` the filmstrip is read from the new proxy in the same run and the thumbnail is taken from the proxy. Anamorphic footage is made square-pixel. A frame that fails to convert fails the ingest. **Not set:** vfx-transcode when it is installed on the worker and `color_args` is the default; otherwise built-in. **`"vfx_transcode"`:** always; the ingest fails if vfx-transcode is missing or `color_args` is customised. **`"builtin"`:** one oiiotool run per frame. A display-referred main (a movie, or a DPX, TIFF, PNG or JPG sequence) is first converted to `working_colorspace` EXRs in the worker's temp folder. With a supplied proxy, thumbnail and filmstrip come from the proxy and vfx-transcode is not run. Stills always use the built-in path. |
| `latlong_proxy_size` | `[width, height]`, 2:1 | `[4096, 2048]` | Size of the proxy generated for a lat-long Still (HDRI). Its thumbnail is 960 × 480. |
| `working_colorspace` | string | `ACES - ACEScg` | Scene-linear colour space that display-referred mains (movies, PNG/TIFF/DPX/JPG sequences) are converted to before vfx-transcode makes their previews. Use the name from the OCIO config. |
| `display`, `view`, `looks` | strings | not used | vfx-transcode only: convert with an OCIO display/view (ACES 1.3+ configs) instead of `output_colorspace`. `""` means the config's default. |
| `description` | string | | Informational only; TinyLib does not display or use it. |

### deadline

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `command` | path | `deadlinecommand` | Deadline command-line executable. |
| `worker_script` | path | none (required) | `worker.py` on a farm-accessible share. Must exist when submitting. |
| `spool_root` | path | none (required) | Shared folder for job manifests and their `.submission.json` and `.status.json`. Must exist when submitting. |
| `pool` | string | `none` | Deadline pool. |
| `group` | string | `none` | Deadline group. |
| `priority` | integer | `50` | Job priority. |
| `python_version` | string | `3.10` | Version for the Deadline Python plugin. |
| `web_service` | object | not used | Submit through the Deadline Web Service instead of `command`; see below. |

Values containing a line break or a double quote are rejected. See [Deadline 10.4](deployment.md#deadline-104).

#### web_service

```json
"web_service": {
  "url": "http://192.168.100.20:8081",
  "require_authentication": false,
  "verify_ssl": false,
  "user": "",
  "password": "${DEADLINE_WEB_PASSWORD}"
}
```

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `url` | string | empty | Web Service address, `http://host:port` or `https://host:port`. Empty turns the Web Service off. |
| `require_authentication` | boolean | `false` | Send `user` and `password` with HTTP basic authentication. Turn on when the Web Service requires authentication. |
| `verify_ssl` | boolean | `true` | `false` accepts any certificate on an `https` URL (self-signed). No effect on `http`. |
| `user` | string | the workstation login | Deadline user name. Used for authentication and as the job's user. |
| `password` | string | empty | Web Service password for `user`. |

With `url` set, every submission tries the Web Service first: no `deadlinecommand` process starts, and each job is confirmed by the job ID the Web Service returns. TinyLib falls back to `command` whenever the Web Service certainly did not create the job:

- `url` is not an `http://` or `https://` address;
- no connection within 3 seconds (not running, wrong address, firewall);
- the request could not be sent;
- the Web Service answered with an error, e.g. 401 for refused credentials or 500 when it cannot reach the repository.

In a folder batch, jobs the Web Service already confirmed stay submitted and only the remaining ones go to `deadlinecommand`. The progress window and the log say why the Web Service was skipped.

TinyLib does not fall back when the request was sent but the answer was lost (no answer within 30 seconds, or the connection dropped), or the answer had no job ID, because the job may exist then. Check Monitor before retrying.

Without the Web Service, a folder batch is submitted with one `deadlinecommand -SubmitMultipleJobs` call, so Deadline starts and connects to the repository once for the whole batch.

The browser configuration is readable by every artist. Do not write the password into it; reference an environment variable set on workstations, e.g. `"${DEADLINE_WEB_PASSWORD}"`.

## Security file

Named by `security` in `tinylib.ini`. Artists need read access; only administrators should be able to write it (see [security setup](security.md)). Changes take effect on **Refresh**.

```json
{
  "access": {
    "groups": ["admins", "managers", "users", "restricted", "farm"],
    "users": {
      "studio\\jane": ["admins"],
      "studio\\artist": ["users"],
      "studio\\deadline": ["farm"]
    },
    "defaults": {"view": ["users", "managers", "admins", "farm"], "ingest": ["admins", "farm"]}
  },
  "libraries": [
    {
      "name": "Elements",
      "root": "//studio/library/elements",
      "read_only": false,
      "permissions": {"edit": ["admins"]}
    }
  ]
}
```

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `access.groups` | list of strings | `admins`, `managers`, `users`, `restricted` | Groups offered in **Access rights…**. Saving there requires the four default groups. |
| `access.users` | object: account → list of groups | `{}` | Windows accounts as `DOMAIN\\login` (JSON-escaped backslash), case-insensitive. With no users, nobody has access. Unknown accounts have no access. |
| `access.defaults` | object: capability → list of groups | see below | Studio-wide grants. A capability not listed uses the built-in default. |
| `libraries` | list of objects | `[]` | Libraries that can receive grants. |
| `libraries[].root` | path | required | Matched against the browser configuration's library roots. Relative paths resolve beside the security file; environment variables are expanded. |
| `libraries[].name` | string | | Shown by `python -m tinylib.admin show`. |
| `libraries[].read_only` | boolean | `false` | Disables ingest and editing for everyone, whatever the browser configuration says. |
| `libraries[].permissions` | object: capability → list of groups | `{}` | Per-library overrides of `access.defaults`. |

### Capabilities

`access.defaults` and `libraries[].permissions` use the same eight keys:

| Capability | Built-in default | Allows |
| --- | --- | --- |
| `view` | users, managers, admins | Seeing the library and previewing its assets. Required for every other capability. |
| `ingest` | admins | Ingesting into the library (workstation and farm worker). |
| `edit` | managers, admins | Editing asset properties, metadata writes. |
| `actions` | users, managers, admins | Running studio actions on its assets. |
| `copy` | users, managers, admins | Taking its assets out of TinyLib: dragging assets to other applications, **Ctrl+C** and collection **Copy** of paths (in the **Paths** notation), **Ctrl+C** of a player frame, and actions that declare `"permissions": ["copy"]` (the bundled Copy main paths and Copy Assets). Without it the library can still be browsed and played, and assets dragged into collections. |
| `version_fields` | admins | Seeing and editing fields whose names start with `version`. |
| `ingest_fields` | managers, admins | Seeing and editing fields whose names start with `ingest`. |
| `acquire_fields` | managers, admins | Seeing and editing fields whose names start with `acquire`. |

- Members of `admins` have every capability on every library. `read_only` and an action's `allow_only` still apply to them.
- Other users get the union of their groups' grants. `restricted` has no default grants and does not remove grants from another group.
- A capability missing from a library's `permissions` inherits the studio default; an explicit `[]` denies all non-admins.
- A library not listed in the security file grants nothing to non-admins.
- To let a group see a library without copying from it, grant it `view` but not `copy`, e.g. `"view": ["users", "managers", "admins", "restricted"]` and `"copy": ["users", "managers", "admins"]`. Grants add up, so this works for accounts that are only in `restricted`.
- Old per-action mappings (an object instead of a list under `actions`) are ignored in favor of the studio `actions` default.

### Failing closed

Nobody gets access, and the status line explains why, when `tinylib.ini` is missing or incomplete, or the security file is missing, unreadable, not a JSON object, has a non-object `access.users`, has a user whose groups are not a list of names, has a `libraries` entry without `root`, or has no users. TinyLib never creates an administrator by itself; use `python -m tinylib.admin add-user`.
