# Studio actions and access

TinyLib identifies the current Windows account through the Windows API, using `DOMAIN\login` (or `COMPUTER\login`). It does not prompt for a password. Identity matching is case-insensitive. The studio configuration stores group membership and library grants; personal preferences never grant access.

Users, groups, libraries and grants live in the studio security file named by `tinylib.ini` in the app folder. See [security setup](security.md) for the files, their Windows permissions, and how to add the first administrator with `python -m tinylib.admin add-user`. TinyLib never makes anyone an administrator automatically. If the security file is missing or has no users, nobody has access. Unknown accounts have no access. Admins always have every library permission; action `allow_only` restrictions still apply to them. Other users receive the union of their groups' grants; restricted membership does not subtract grants from another group.

Admins use **Access rights…** to add Windows accounts, assign groups, and set studio defaults and library overrides for visibility, ingestion, editing, actions, and the version/ingest/acquire field groups. At least one admin must remain. Additional groups can be declared in the configuration and appear in the editor. Default grants: users/managers/admins can view and run actions; only admins can ingest; managers/admins can edit. Only admins see/edit version fields; managers/admins see/edit ingest and acquire fields. Restricted has no default grants. A missing library rule inherits the studio default; an explicit empty list denies non-admins. `read_only` disables ingestion and editing for everyone.

The asset properties editor uses the view/edit grants for color-space, keyword and metadata writes. Read-only libraries cannot be edited, including by admins. Name, library, category, type and footage frame range are always read-only. **Edit** creates a `lock.<windows-user>.txt` file beside `tinylib_data.json`; **Write** rechecks permissions, preserves media paths and asset identity, commits atomically, and removes that lock. Other users cannot edit or publish an ingest while it exists. Admins are offered an explicit override that removes stale user lock files before creating their own.

Example configuration fragment (merge with your tools and processing settings):

```json
{
  "action_roots": ["//studio/tools/tinylib-actions"],
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

The permissions source is always the `security` file named in `tinylib.ini`. Browser configurations, `*.local.json` overrides and `TINYLIB_CONFIG` cannot change it, and the old `security_config` key is ignored. A library absent from the security file grants nothing to non-admins, and its `read_only` flag there wins over any browser configuration.

Refresh reloads permissions and visible libraries. Preview, action execution, ingestion, and Deadline submission recheck grants. An action requires visibility and the library actions grant for **every** selected asset's library. The farm worker checks its own Windows account's view/ingest grants before processing. Give the worker service account a dedicated group with view and ingest, not `admins` (see [security setup](security.md#farm)). The worker uses tool paths and processing profiles from its own configuration, never from the job manifest.

Protect the studio configuration and action directories with filesystem permissions so ordinary users cannot edit them. TinyLib's checks govern its UI and processing entry points; they do not replace storage permissions or prevent direct access to media by an account already allowed to read it.

## Action packages

`action_roots` lists folders of action packages. A relative entry resolves beside the browser configuration. Start an entry with `{app}` to resolve it inside the TinyLib install folder instead, for example `"{app}/actions"` for the bundled actions. The same `{app}` prefix works for `tools` paths, such as `"{app}/executables/ffmpeg/ffmpeg.exe"`. Without `action_roots`, TinyLib uses `{app}/actions`.

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

Optional `allow_only` restricts execution to members of the listed groups, e.g. `"allow_only": ["admins"]`. Omit it for no action-specific restriction; `[]` denies everyone. The restriction intersects the library actions grant and is rechecked before importing action code, including for admins.

Optional `icon` points to a PNG within the action folder. Optional `hosts` restricts execution, for example `["nuke"]`; recognized hosts are `nuke`, `houdini`, `maya`, and `standalone`. Omit it to allow all hosts. Optional `max_selection` limits selection size. Omitted filter fields impose no restriction. Paths for code, configuration, and icons must remain inside the action folder.

Example `action.py`, with `config.json` containing `{}`:

```python
def run(assets, context, config):
    # assets: copies of normalized TinyLib records (main, proxy, first, last,
    # library_root, colorspace, metadata, tags, etc.).
    # context: identity, parent Qt widget, settings, collection name or None.
    # config: this action's configuration JSON.
    return "Processed %d assets" % len(assets)
```

The properties dropdown operates on the main-view selection. The collection dropdown operates on the whole collection. A collection containing unresolved references cannot run an action partially. Bundled actions create Nuke main/highres Read nodes or copy main paths to the clipboard. New actions use the library actions grant automatically; there is no per-action library grant table. Put action-specific group restrictions in its manifest. Old per-action grant mappings are ignored in favor of the studio actions default; saving Access rights replaces them with the new rules.

Studio-wide rules use `access.defaults` with the same seven keys as `libraries[].permissions`. In Access rights, the Studio defaults rows change these grants. Enable Override defaults on a library row to set its own rule; disable it to inherit. Prefix field rules apply to any field beginning with `version`, `ingest`, or `acquire`, including custom metadata entries. Hidden fields are preserved when permitted fields are edited, and unauthorized field changes are rejected on save.
