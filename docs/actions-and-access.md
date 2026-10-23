# Studio actions and access

TinyLib identifies the current Windows account through the Windows API, using `DOMAIN\login` (or `COMPUTER\login`). It does not prompt for a password. Identity matching is case-insensitive. The studio security file stores group membership and library grants; the browser configuration and personal preferences never grant access.

Users, groups, libraries and grants live in the studio security file named by `tinylib.ini` in the app folder. See [security setup](security.md) for the files, their Windows permissions, and how to add the first administrator with `python -m tinylib.admin add-user`. TinyLib never makes anyone an administrator automatically. If the security file is missing or has no users, nobody has access. Unknown accounts have no access. Admins always have every library permission; action `allow_only` restrictions still apply to them. Other users receive the union of their groups' grants; restricted membership does not subtract grants from another group.

Admins use **Access rights…** to add Windows accounts, assign groups, and set studio defaults and library overrides for visibility, ingestion, editing, actions, and the version/ingest/acquire field groups. At least one admin must remain. Additional groups can be declared in `access.groups` in the security file and appear in the editor. Default grants: users/managers/admins can view and run actions; only admins can ingest; managers/admins can edit. Only admins see/edit version fields; managers/admins see/edit ingest and acquire fields. Restricted has no default grants. A missing library rule inherits the studio default; an explicit empty list denies non-admins. `read_only` disables ingestion and editing for everyone.

The asset properties editor uses the view/edit grants for color-space, keyword and metadata writes. Read-only libraries cannot be edited, including by admins. Name, library, category, type and footage frame range are always read-only. **Edit** creates a `lock.<windows-user>.txt` file beside `tinylib_data.json`; **Write** rechecks permissions, preserves media paths and asset identity, commits atomically, and removes that lock. Other users cannot edit or publish an ingest while it exists. Admins are offered an explicit override that removes stale user lock files before creating their own.

Example security file (the full key list is in the [configuration reference](configuration.md#security-file)):

```json
{
  "access": {
    "groups": ["admins", "managers", "users", "restricted", "farm"],
    "users": {
      "studio\\administrator": ["admins"],
      "studio\\artist": ["users"],
      "studio\\deadline": ["farm"]
    }
  },
  "libraries": [{
    "name": "Elements",
    "root": "//studio/library/elements",
    "permissions": {
      "view": ["users", "managers", "admins"],
      "ingest": ["admins"],
      "edit": ["managers", "admins"],
      "actions": ["users", "managers", "admins"],
      "version_fields": ["admins"],
      "ingest_fields": ["managers", "admins"],
      "acquire_fields": ["managers", "admins"]
    }
  }]
}
```

The browser configuration lists the same library, so it appears in the browser, and the action folders:

```json
{
  "action_roots": ["//studio/tools/tinylib-actions"],
  "libraries": [{"name": "Elements", "root": "//studio/library/elements"}]
}
```

The permissions source is always the `security` file named in `tinylib.ini`. Browser configurations, `*.local.json` overrides and `TINYLIB_CONFIG` cannot change it, and the old `security_config` key is ignored. A library absent from the security file grants nothing to non-admins, and its `read_only` flag there wins over any browser configuration.

Refresh reloads permissions and visible libraries. Preview, action execution, ingestion, and Deadline submission recheck grants. An action requires visibility and the library actions grant for **every** selected asset's library. The farm worker checks its own Windows account's view/ingest grants before processing. Give the worker service account a dedicated group with view and ingest, not `admins` (see [security setup](security.md#farm)). The worker uses tool paths and processing profiles from its own configuration, never from the job manifest.

Protect the studio configuration and action directories with filesystem permissions so ordinary users cannot edit them. TinyLib's checks govern its UI and processing entry points; they do not replace storage permissions or prevent direct access to media by an account already allowed to read it.

## Action packages

`action_roots` lists folders of action packages. A relative entry resolves beside the browser configuration. Start an entry with `{app}` to resolve it inside the TinyLib install folder instead, for example `"{app}/actions"` for the bundled actions. Tool paths belong in `[tools]` of `tinylib.ini`, where a relative path already resolves in the install folder. Without `action_roots`, TinyLib uses `{app}/actions`.

Each immediate subfolder of an `action_roots` folder contains `manifest.json`, `config.json`, and Python code, with an optional PNG icon. Discovery reads manifests without executing Python. Duplicate action IDs are rejected. Actions are trusted studio code running inside the host process.

Example `manifest.json`:

```json
{
  "id": "studio.example",
  "name": "Example action",
  "version": "1.0.0",
  "category": "Utilities",
  "entrypoint": "action.py",
  "callable": "run",
  "config": "config.json",
  "asset_filter": {
    "kinds": ["footage", "still"],
    "extensions": [".exr"],
    "requires": ["main"],
    "min_selection": 1
  }
}
```

Optional `permissions` lists library capabilities the action needs besides `actions`, e.g. `"permissions": ["copy"]` for an action that takes files or paths out of TinyLib; the action is offered only when the user has them on every library involved. The bundled Copy main paths and Copy Assets declare `copy`.

Optional `tools` lists programs the action runs, by their name under `[tools]` in `tinylib.ini`, e.g. `"tools": ["rv"]`; the action is offered only when they are found (see [external tools](#external-tools)).

Optional `context_menu` puts the action in the right-click menu: `true` for the main view, the collection panel and the player windows, or `{"label": "Open in RV", "where": ["main", "collection", "player"]}` to choose the places and the menu text (default: the action name). The menu runs the action on the selected assets (right-clicking an unselected asset selects it first), or in a player on the asset it shows. Actions are grouped by category and filtered like the dropdowns: asset filter, host, permissions and tools.

Optional `"settings": true` gives the action a settings window: a **<name> settings…** entry at the end of both action dropdowns and of the right-click menus where the action appears, offered whatever is selected (host, `allow_only` and `tools` still apply; no library permission is needed). It calls the action with no assets and `context['invoked'] == 'settings'`; keep the settings in the user's preferences (`context['parent'].preferences.set_display(...)`).

Optional `allow_only` restricts execution to members of the listed groups, e.g. `"allow_only": ["admins"]`. Omit it for no action-specific restriction; `[]` denies everyone. The restriction intersects the library actions grant and is rechecked before importing action code, including for admins.

`id`, `name`, `version` and `category` are required; `id` may contain only letters, digits, `.`, `_` and `-`. `entrypoint` (default `action.py`) and `config` (default `config.json`) must exist. `callable` (default `run`) must be a Python function name.

Optional `icon` points to a PNG within the action folder. Optional `hosts` restricts execution, for example `["nuke"]`; recognized hosts are `nuke`, `houdini`, `maya`, and `standalone`. Omit it to allow all hosts. Optional `asset_filter.max_selection` limits selection size. `asset_filter.whole_view: false` keeps the action off the properties dropdown while nothing is selected, so it never runs on everything the main view shows (collections still run it on the whole collection). Omitted filter fields impose no restriction. Paths for code, configuration, and icons must remain inside the action folder.

Example `action.py`, with `config.json` containing `{}`:

```python
def run(assets, context, config):
    # assets: copies of normalized TinyLib records (main, proxy, first, last,
    # library_root, colorspace, metadata, tags, etc.).
    # context: identity, parent Qt widget, settings, collection name or None,
    # trim: a function returning an asset's (in, out) points, or None,
    # whole_view: True when nothing is selected in the main view,
    # collection_selected: the assets selected in the collection panel (collection runs),
    # invoked: "dropdown" (Run) or "context_menu" (right-click),
    # place: "main", "collection" or "player"; window: the player window (player menu).
    # config: this action's configuration JSON.
    return "Processed %d assets" % len(assets)
```

The properties dropdown operates on the main-view selection. The collection dropdown operates on the whole collection. A collection containing unresolved references cannot run an action partially. Bundled actions create Nuke main/highres Read nodes, copy main paths to the clipboard, [copy asset files to a folder](user-guide.md#copy-assets), or [publish footage and stills to AYON](ayon-publish.md). New actions use the library actions grant automatically; there is no per-action library grant table. Put action-specific group restrictions in its manifest. Old per-action grant mappings are ignored in favor of the studio actions default; saving Access rights replaces them with the new rules.

### External tools

Programs an action runs (players such as RV, DJV or mrViewer, or anything else) are named under `[tools]` in `tinylib.ini`, which only administrators can write, so an action never decides which executable runs: `rv = C:/Program Files/Autodesk/RV-2025.0.0/bin/rv.exe`. A tool not set there is looked up on PATH under its own name. Declare them in the manifest (`"tools": ["rv"]`) and start them with `tinylib.external_tools`:

```python
from tinylib.external_tools import asset_arguments, launch

def run(assets, context, config):
    arguments = asset_arguments(assets, ['{rv}'], component='main', trim_for=context.get('trim'))
    launch(context['settings'], 'rv', arguments)
    return 'Opened %d assets in RV' % len(assets)
```

`launch(settings, name, arguments)` starts the program detached (TinyLib does not wait, and closing TinyLib or Nuke does not close it), without a shell, and without the host's `PYTHONHOME`, `PYTHONPATH` and Qt plugin variables, which would make another program load Nuke's Python or Qt (`keep_host_variables=True` keeps them). `resolve(settings, name)` returns the full path or raises `ToolMissing` saying what to set. `asset_arguments(assets, template, component, trim_for)` fills a list template for each asset with `{first_file}` (the file, or the first frame's file), `{path}` (`####`), `{printf}` (`%04d`), `{rv}` (`name.1001-1100#.exr`), `{xstudio}` (`name.####.exr=1001-1100`), `{first}`, `{last}`, `{count}`, `{fps}`, `{folder}` and `{name}`; frame ranges follow the in/out points. A nested list is an optional group, left out when one of its tokens is empty, e.g. `["-in", "{first}"]` for a movie.

Studio-wide rules use `access.defaults` with the same eight keys as `libraries[].permissions`. In Access rights, the Studio defaults rows change these grants. Enable Override defaults on a library row to set its own rule; disable it to inherit. Prefix field rules apply to any field beginning with `version`, `ingest`, or `acquire`, including custom metadata entries. Hidden fields are preserved when permitted fields are edited, and unauthorized field changes are rejected on save.
