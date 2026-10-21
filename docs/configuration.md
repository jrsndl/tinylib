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
| `OCIO` | Not read by TinyLib directly; the public profiles use `"${OCIO}"` as their `ocio_config`. |

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

`{app}` is the TinyLib install folder, the one containing `menu.py` and the `tinylib` package. Write it at the start of the value: `"{app}/actions"`, `"{app}/executables/ffmpeg/ffmpeg.exe"`. Other path values (`ocio_config`, Deadline paths) are used as written.

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
| [`extension_groups`](#extension_groups) | object | built-in groups | Override the file extensions of the predefined groups. |
| [`tools`](#tools) | object of paths | `{}` | External executables. Prefer `[tools]` in `tinylib.ini`, which overrides this. |
| [`profiles`](#profiles) | object | `{}` | Processing profiles offered at ingest. |
| [`deadline`](#deadline) | object | `{}` | Deadline submission. |
| `action_roots` | list of folder paths | `["{app}/actions"]` | Folders of action packages; see [action packages](actions-and-access.md#action-packages). |
| `tile_template` | string | `{name}\n{type} · {width} × {height}` | Initial tile caption template. A user's own **Tile text…** setting wins. An invalid template falls back to the default. Tokens are listed in the [user guide](user-guide.md#tile-text). |
| `player_backend` | `"interactive"` or `"ffplay"` | `"interactive"` | `"ffplay"` launches the external ffplay player instead of the built-in review player. Any other value means interactive. |
| `library_defaults` | object | `{}` | Starting entry for **Create New Library…** with "Studio defaults", for example `{"read_only": false, "legacy_roots": []}`. Copied as written; name and root come from the dialog. |
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
| `name` | string | required | Display name. Must be unique for **Create New Library…**. |
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
| `ffmpeg` | `ffmpeg` | Generating proxies, thumbnails and filmstrips; decoding in the review player. |
| `ffprobe` | `ffprobe` | Probing containers (ingest, Rescan Metadata) and checking generated media. The player uses `ffprobe` beside `ffmpeg` when this key is not set. |
| `oiiotool` | `oiiotool` | Color conversion of image files; probing images and sequences. |
| `vfx_transcode` | `vfx-transcode` | Image-sequence proxies for profiles with `"proxy_backend": "vfx_transcode"`. |
| `ffplay` | `ffplay` beside `ffmpeg`, else `ffplay` on PATH | Only with `"player_backend": "ffplay"`. |

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
| `ocio_config` | path | none | OCIO config file. Must exist on the worker whenever previews are generated from image files (stills and image sequences), and for `proxy_backend` `vfx_transcode`. Footage containers are converted with FFmpeg and do not use it. |
| `output_colorspace` | string | `Output - Rec.709` | OCIO display color space of generated previews. |
| `color_args` | list of strings | `["--colorconvert", "{input_colorspace}", "{output_colorspace}"]` | oiiotool arguments for the color conversion. `{input_colorspace}` is the main color space chosen at ingest; `{output_colorspace}` is `output_colorspace`. An empty list is an error. |
| `thumbnail_frame` | `"largest"` or `"middle"` | `"largest"` | Frame used for a generated thumbnail: the largest source file, or the middle frame. |
| `proxy_backend` | `"vfx_transcode"` | built-in | `"vfx_transcode"` makes image-sequence footage proxies with `tools.vfx_transcode`. Otherwise frames are converted with oiiotool and encoded with FFmpeg. |
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

Values containing a line break or a double quote are rejected. See [Deadline 10.4](deployment.md#deadline-104).

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

`access.defaults` and `libraries[].permissions` use the same seven keys:

| Capability | Built-in default | Allows |
| --- | --- | --- |
| `view` | users, managers, admins | Seeing the library and previewing its assets. Required for every other capability. |
| `ingest` | admins | Ingesting into the library (workstation and farm worker). |
| `edit` | managers, admins | Editing asset properties, metadata writes. |
| `actions` | users, managers, admins | Running studio actions on its assets. |
| `version_fields` | admins | Seeing and editing fields whose names start with `version`. |
| `ingest_fields` | managers, admins | Seeing and editing fields whose names start with `ingest`. |
| `acquire_fields` | managers, admins | Seeing and editing fields whose names start with `acquire`. |

- Members of `admins` have every capability on every library. `read_only` and an action's `allow_only` still apply to them.
- Other users get the union of their groups' grants. `restricted` has no default grants and does not remove grants from another group.
- A capability missing from a library's `permissions` inherits the studio default; an explicit `[]` denies all non-admins.
- A library not listed in the security file grants nothing to non-admins.
- Old per-action mappings (an object instead of a list under `actions`) are ignored in favor of the studio `actions` default.

### Failing closed

Nobody gets access, and the status line explains why, when `tinylib.ini` is missing or incomplete, or the security file is missing, unreadable, not a JSON object, has a non-object `access.users`, has a user whose groups are not a list of names, has a `libraries` entry without `root`, or has no users. TinyLib never creates an administrator by itself; use `python -m tinylib.admin add-user`.
