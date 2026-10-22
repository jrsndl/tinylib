# Publish to AYON

**Publish to AYON** is a bundled action (`actions/ayon_publish`, id `ayon.publish`) that publishes the selected footage and stills to AYON, in Nuke or in the standalone browser. Pick it in the properties panel or the collection action dropdown.

Each asset becomes one new AYON version:

| TinyLib | AYON |
| --- | --- |
| Footage | product type `render` |
| Still | product type `image` |
| main, proxy, highres | one representation each, as enabled under **Components** (main and proxy by default) |
| thumbnail | the version thumbnail |

The product name is the product type and a variant, for example `rendersmoke_puff_01`. Two selected assets of the same product type need different variants, or they would be one version. An asset without a proxy or highres is published without it. A proxy whose representation name is the main's (a JPEG still with a JPEG proxy) is not a representation of its own; its tags are added to the main's. Any other two components with the same representation name are refused: rename one under Components.

The action needs a main and a thumbnail. The filmstrip is not published.

## The window

- **Accounts** shows who is signed in to AYON and ftrack, and where the sign-in came from.
- **Publish into**: pick the project, then a task (the green entries) in the folder tree. Type in the search box to narrow the tree. In Nuke started from AYON, the window opens on the Nuke session's project, folder and task; otherwise on the target of your last publish.
- **Components**: which components become representations, and for each its representation name (`{extension}` is the file extension), tags (separated by `;`) and colour space (`{colorspace}` is the asset's own). The `ftrackreview` tag is left out when there are no ftrack credentials. The rows start from `components` in the action's `config.json`:

  | On | Component | Representation | Tags | Colorspace |
  | --- | --- | --- | --- | --- |
  | on | main | `{extension}` | `passing` | `ACEScg` |
  | on | proxy | `{extension}` | `passing;ftrackreview;webreview` | `Output - Rec.709` |
  | off | highres | `{extension}` | `passing` | `ACEScg` |

- **Assets**: for each asset, the product type and the editable variant, version and comment. The variant starts as the asset name (not its label), with characters other than letters, digits and `_` turned into `_`. The version starts as the asset's version number, or 1; empty publishes the next version. The comment, the version comment, starts as `TinyLib: <library> / <asset name>` (config `comment`).
- **Deadline Publish** sends the publish to the farm instead of running it on this computer (see [Deadline](#deadline)).
- **Each Asset Separate** writes one CSV file per asset and publishes each on its own (one after another here, or one Deadline job each), so one failing asset does not stop the others. Off, all assets go in one CSV file and one publish.
- **Publish** writes the CSV file(s) and runs AYON's CSV ingest, showing its output, or submits the Deadline jobs. A local publish keeps the window open until it finishes; **Stop** ends it and skips the assets still waiting.

Both checkboxes, and the target, are remembered for your next publish.

## Credentials

**AYON** credentials come from, first found:

1. the environment: `AYON_SERVER_URL` and `AYON_API_KEY`. Nuke started from the AYON launcher has them; TinyLib detects this and uses them, and there is nothing to sign in to.
2. your own sign-in: **Sign in…** asks for your AYON user name and password. Only the session token is kept, in the Windows Credential Manager under `TinyLib/ayon/<server>`, never the password. When the session expires, TinyLib asks for the password again.

**ftrack** is optional. With ftrack credentials, the components keep their `ftrackreview` tag and the credentials are passed to the publish, so the ftrack reviewable is published as well; without them the tag is left out. They come from `FTRACK_SERVER`, `FTRACK_API_USER` and `FTRACK_API_KEY` in the environment, or from **Sign in…**: in the web browser (one click when you are signed in to ftrack there) or by typing your personal API key. The key is kept in the Windows Credential Manager under `TinyLib/ftrack/<server>`. **Sign out** forgets a stored sign-in.

The server addresses come from the environment, else from `ayon_server_url` and `ftrack_server` in the action's `config.json`, else from what you type in the sign-in window.

## How it publishes

TinyLib writes a CSV file and runs the AYON launcher's CSV ingest of the traypublisher addon, the same way IngestDesktop does:

```text
ayon_console addon traypublisher ingestcsv --filepath <csv> --project <project>
    --folder-path <folder> --task <task> --preset Default --ignore-validators
```

The CSV file and the output of the publish (`.log`) are kept in `%LOCALAPPDATA%\TinyLib\ayon_publish` (`log_folder` changes it).

The AYON launcher is found in this order: `ayon_executable` in the action's `config.json`; `ayon_console` in the `[tools]` section of `tinylib.ini`; `ayon_console.exe` beside `AYON_EXECUTABLE`, which the AYON launcher sets for the programs it starts. Standalone TinyLib needs one of the first two:

```ini
[tools]
ayon_console = C:/Program Files/Ynput/AYON 1.6.3/ayon_console.exe
```

The publish runs with TinyLib's environment, without Nuke's Python and AYON context variables (`strip_environment`), and with the AYON (and ftrack) credentials.

## Deadline

With **Deadline Publish**, each CSV file becomes a Deadline job of the **CommandLine** plugin that runs the same `ayon_console … ingestcsv` command on a worker. Jobs are submitted like ingests: through the Deadline Web Service when `deadline.web_service` is configured, else (and for any job the Web Service did not take) with one `deadlinecommand -SubmitMultipleJobs` call. Pool, group and priority come from `deadline` in the browser configuration; the action's `deadline` object overrides them.

- The CSV, `.job`, `.plugin` and `.log` files are written to `deadline_csv_folder`, else to `ayon_publish` in the Deadline `spool_root` of the browser configuration. Workers must be able to read it, and the asset files the CSV points to.
- The worker runs `deadline_ayon_executable`, else the AYON launcher found on this computer; set it when workers have AYON somewhere else.
- The job environment holds `AYON_SERVER_URL` and, with `deadline_send_credentials` (default on), your `AYON_API_KEY` and ftrack credentials, because the worker publishes as you. They are visible in the job's properties in Deadline Monitor to anyone who can see the job. Studios whose workers have their own AYON login can turn it off. `deadline_environment` adds variables, e.g. `{"AYON_SITE_ID": "farm"}`.
- A failed job can be requeued in Monitor; the CSV stays in the folder.

## AYON setup

The CSV ingest preset (`preset`, default `Default`) in the AYON traypublisher settings must list the representation names TinyLib uses, with their file extensions: with the default `{extension}` names, the file types of your mains, proxies and highres (`exr`, `mov`, `mp4`, `jpg`, `png`, …). The tags must suit the preset's review profiles; the defaults are those of IngestDesktop.

## Configuration

`actions/ayon_publish/config.json` (keys starting with `_` are ignored):

| Key | Default | Meaning |
| --- | --- | --- |
| `ayon_server_url` | `""` | AYON server when the environment has none. |
| `ftrack_server` | `""` | ftrack server when the environment has none. |
| `ayon_executable` | `""` | AYON launcher console executable; see above. |
| `preset` | `Default` | CSV ingest preset in AYON. |
| `ignore_validators` | `true` | Pass `--ignore-validators`. |
| `csv_folder_path`, `csv_task` | `""` | Context of the CSV ingest itself; empty uses the picked folder and task. IngestDesktop uses `/edit/csvingest` and `csvingest`. |
| `components` | main, proxy and highres as above | The Components rows: `enabled`, `component` (`main`, `proxy`, `highres`), `representation`, `tags`, `colorspace`. |
| `ftrack_review_tag` | `ftrackreview` | Left out of the tags when there are no ftrack credentials. |
| `colorspace_map` | `{}` | Renames the asset's colour space for `{colorspace}`, e.g. `{"ACEScg": "ACES - ACEScg"}`. |
| `default_fps` | `25.0` | FPS when the asset has no frame rate (stills). |
| `still_frame` | `1` | Frame start and end of stills. |
| `comment` | `TinyLib: {library} / {name}` | The comment each asset starts with; `{library}`, `{name}`, `{label}`, `{category}`, `{id}`. |
| `log_folder` | `""` | Folder for the CSV and log files of a local publish. |
| `deadline_csv_folder` | `""` | Farm-readable folder for Deadline publishes; empty uses `ayon_publish` in the Deadline `spool_root`. |
| `deadline_ayon_executable` | `""` | `ayon_console` on the workers; empty uses the one found on this computer. |
| `deadline_send_credentials` | `true` | Put your AYON and ftrack credentials in the job environment. |
| `deadline_environment` | `{}` | More environment variables for the jobs. |
| `deadline` | `{}` | `pool`, `group`, `priority` overriding the browser configuration's `deadline`. |
| `strip_environment` | Python and AYON context variables | Variables removed from the publish environment. |

## Requirements

- In Nuke started from AYON, everything is already there.
- Standalone, and in Nuke without AYON, TinyLib needs the `ayon_api` and `keyring` Python modules: install with the `ayon` extra (`uv sync --extra pyside6 --extra ayon`). `build.bat` includes them in `TinyLib.exe`.
- The ftrack key is checked when the `ftrack_api` module is available; otherwise a wrong key shows up as a failed ftrack step in the publish log.

The sign-in windows and credential handling are adapted from Submission Helper, the CSV ingest from IngestDesktop.
