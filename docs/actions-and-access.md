# Studio actions and access

TinyLib identifies the current Windows account through the Windows API, using `DOMAIN\login` (or `COMPUTER\login`). It does not prompt for a password. Identity matching is case-insensitive. The studio configuration stores group membership and library grants; personal preferences never grant access.

The first launch against a configuration with no assigned users registers that account in all four groups: `admins`, `managers`, `users`, and `restricted`. Perform that first launch as the intended studio administrator. Once users exist, unknown accounts have no access. Admins always have every permission. Other users receive the union of their groups' grants; restricted membership does not subtract grants from another group.

Admins use **Access rights…** to add Windows accounts, assign groups, and grant library visibility, ingestion, and individual actions. At least one admin must remain. Additional groups can be declared in the configuration and appear in the editor. The default library grants allow managers/users to browse and run bundled actions; only managers may ingest. `read_only` still disables ingestion for everyone.

Example configuration fragment (merge with your tools and processing settings):

```json
{
  "action_roots": ["//studio/tools/tinylib-actions"],
  "access": {
    "groups": ["admins", "managers", "users", "restricted"],
    "users": {
      "studio\\administrator": ["admins"],
      "studio\\artist": ["users"],
      "studio\\farm": ["managers"]
    }
  },
  "libraries": [{
    "name": "Elements",
    "root": "//studio/library/elements",
    "permissions": {
      "view": ["managers", "users"],
      "ingest": ["managers"],
      "actions": {"nuke.read": ["users", "managers"], "clipboard.paths": ["users"]}
    }
  }]
}
```

By default, the selected studio configuration is also the permissions source. Set `security_config` to the central studio JSON when using a separate browser configuration (as the demo/performance examples do). Relative paths resolve beside the configuration. Local JSON overrides are supported. Set `TINYLIB_CONFIG` centrally for both workstations and Deadline workers.

Refresh reloads permissions and visible libraries. Preview, action execution, ingestion, and Deadline submission recheck grants. An action requires visibility and its action grant for **every** selected asset's library. The farm worker checks its own Windows account's view/ingest grants before processing; grant the worker service account access in the same studio configuration.

Protect the studio configuration and action directories with filesystem permissions so ordinary users cannot edit them. TinyLib's checks govern its UI and processing entry points; they do not replace storage permissions or prevent direct access to media by an account already allowed to read it.

## Action packages

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
    "kinds": ["footage", "still", "hdri"],
    "extensions": [".exr"],
    "requires": ["main"],
    "min_selection": 1
  }
}
```

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

The properties dropdown operates on the main-view selection. The collection dropdown operates on the whole collection. A collection containing unresolved references cannot run an action partially. Bundled actions create Nuke main/highres Read nodes or copy main paths to the clipboard. Add each new action ID to the applicable library grants; admins require no explicit grant.
