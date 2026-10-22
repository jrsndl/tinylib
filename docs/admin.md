# Administration

Administrators are members of the `admins` group in the studio security file. Setting up that file, the first administrator and Windows permissions is covered in [security setup](security.md). Groups, grants and action packages are covered in [studio actions and access](actions-and-access.md). Every configuration key is listed in the [configuration reference](configuration.md).

Administrators see three extra controls; they are hidden for everyone else, and each re-checks the security file when used, so an account removed from `admins` loses them at once:

- **Access rights…** edits users, groups, studio defaults and per-library grants in the security file. Saving needs membership of `admins` and Windows write permission on the file. Rows without an account name are rejected, and removing your own admin rights asks for confirmation.
- **Configuration…** opens the active browser configuration file.
- **Library tools**, in the user menu at the bottom-left corner (your login name), described below.
- **Login as…**, in the same user menu, lets you use TinyLib as another user from the security file would: their groups, libraries, actions and edit rights, with your admin controls hidden. The user menu then offers **Log out**, which brings you back to your own account. Your own preferences (ratings, collections) stay in use. Edit locks, the `ingest_user` of ingests and the log name both accounts, e.g. `studio\admin as studio\jan`. Login as and Log out are unavailable while editing.

From a command prompt in the app folder, `python -m tinylib.admin` manages accounts without the browser:

```powershell
python -m tinylib.admin whoami
python -m tinylib.admin show
python -m tinylib.admin add-user "studio\jane" users
python -m tinylib.admin remove-user "studio\joe"
```

## Library tools

Only members of `admins` can use them. The menu entry is hidden for other accounts, and every tool re-reads the security file when it starts, so an account removed from `admins` while TinyLib is open is refused at once.

### Library crosscheck…

Checks every path listed in the selected library's `tinylib_data.json`, including sequence frames and relative texture/file lists, then scans each `library/category/asset` folder for assets missing from the JSON. A progress dialog shows the current record or path; **Cancel** or **Esc** stops after the current filesystem check. The report is saved in your Downloads folder as `<library name>_crosscheck_<datetime>.log`.

### Rescan metadata…

Scans the selected Still and Footage assets, or the whole selected library when no assets are selected. Image files and sequences use the configured `oiiotool`; container footage uses `ffprobe`. Each probe times out after 60 seconds. **Cancel** or **Esc** stops after the current probe.

- Only `frame_rate`, `width` and `height` are compared with, or offered for storage in, `tinylib_data.json`.
- Every successful probe also writes the complete raw and normalized tool result into the asset folder (beside `main/`, never inside it) as `<clean name>_meta.json`. Sequence counters and their preceding separator are removed from the clean name. Sidecars that older versions wrote inside `main/` are ignored when folders are scanned.
- The result view lists discrepancies, sidecars and probe errors. **Write metadata** is offered only when one of the three database fields differs.
- Confirmed changes are written as one atomic database update per library, under the same [edit lock](#edit-locks) and override workflow as **Edit** / **Write**.

### Library sort…

Sorts the selected writable library's schema version 3 database by category and asset name (case-insensitive), then reloads the browser. Select any category to sort its whole library; from "All libraries", choose the library first. Sorting keeps record contents and uses the same edit lock, administrator override and atomic write as editing. Run it again after new ingests.

### Labels from names…

Makes asset labels (the names shown in the browser, see [asset types](asset-types.md#common-asset-fields)) from asset names, in one writable library at a time. Pick the library in the dialog; it starts on the library of the selected category. The asset names and folders never change.

| Option | Default | Effect |
| --- | --- | --- |
| **Only assets with an empty label** | on | Off: labels set by hand are replaced too. |
| **Split words at capitals** | on | `abandonedBakery` → `abandoned bakery`. The capital becomes lowercase; acronyms stay: `HDRISky` → `HDRI sky`. A capital after a space, underscore or dash is kept. |
| **Replace underscores and dashes with spaces** | on | `plhn_indoor-01` → `plhn indoor 01`. |
| **Replace 1–3** | empty | Search and replace pairs: plain text, case-sensitive, applied in order after the options above. An empty replacement removes the text, e.g. `plhn ` → nothing. |

Repeated spaces become one and the ends are trimmed. A label that comes out equal to the name is left empty, which shows the name anyway. The preview lists every asset that gets a new label, with its current and new label, and updates as you change the options. **Apply labels** re-reads the library database and writes them with the same edit lock, administrator override and atomic write as editing, then the browser reloads.

### Keyword cleaner…

Cleans asset keywords in one writable library at a time. Pick the library in the dialog; it starts on the library of the selection, or of the selected category.

**Works on** says what is changed: when assets of that library are selected in the main view, **only those selected assets**; otherwise the whole library. Clear the selection to clean the whole library.

| Option | Default | Effect |
| --- | --- | --- |
| **Remove keywords that are only a number** | on | `01`, `2525` and `1.5` go; `2k` and `v002` stay. |
| **Replace 1–3** | empty | The search matches a **whole** keyword, ignoring case. The keyword is replaced by the replacement; an empty replacement removes it, and a comma-separated one gives several keywords (`abandonedbakery` → `abandoned, bakery`). Applied in order, after removing numbers. |
| **Add keywords** | empty | Comma-separated keywords added to every asset in scope (lowercase, at the end), unless the asset already has them. Applied last. |

The same keyword (ignoring case) is kept once; the order of the other keywords is kept. The preview lists every asset whose keywords change, with its keywords now and after. **Apply keywords** re-reads the library database and writes them with the same edit lock, administrator override and atomic write as editing, then the browser reloads.

### Create new library…

Asks for a name, root folder and settings source: Studio defaults or an existing library. You can change the copied read-only setting.

- It creates a valid empty `tinylib_data.json`, registers the library in both the active browser configuration and the security file, and refreshes the browser.
- Studio defaults start from the browser configuration's optional `library_defaults` and inherit studio permissions. Copying a library keeps its options and permission overrides.
- An existing database and duplicate names or roots are rejected.
- Configuration writes use the shared write locks, and registration is rolled back if a write fails.

The browser configuration that receives the entry is the file actually loaded, so a `*.local.json` override is updated when it is active.

## Edit locks

Entering edit mode (**Edit**, metadata write, library sort) creates `lock.<windows-user>.txt` beside the library's `tinylib_data.json`. The file records the Windows identity, time, computer, process ID and a session token. While it exists:

- other users see who holds the lock and cannot edit or publish an ingest into that library;
- administrators are offered **Override lock**, which removes the existing lock files before creating their own. Use it for locks left by a crashed session.

**Write** rechecks permission, writes the database atomically and removes the lock. **Cancel edit**, closing the browser and quitting Nuke also remove the session's own lock. A failed validation or database write keeps the draft and the lock so the user can correct or cancel. A lock is only removed by the session that created it, unless an administrator overrides it. Unknown database fields are kept, and read-only libraries stay protected even for administrators.

## Write locks

Every write to a shared JSON file (library database, security file, browser configuration, edit locks, preferences) takes a short exclusive lock, `.tinylib-write.lock`, in that file's folder. The lock records the computer, process ID and a token.

- A writer waits up to 15 seconds for the lock. The timeout message names the computer and process holding it.
- A stale lock is broken automatically: at once when its owner process no longer exists on the same computer, otherwise when it is older than 10 minutes. Age compares this computer's clock with the file time on the share, so keep workstation and server clocks synchronised.
- The file is written to a temporary file and then renamed over the original. On Windows shares the rename is retried briefly while another program is reading the file.
- Publishing an ingest rereads the database under the lock and appends the new asset to the current file. Other users' edits and unknown top-level keys are kept.

Network storage must support exclusive file creation and atomic rename. Check this on your file server before running several farm workers at once.

## Legacy databases

Older library databases are read through an adapter. Configure `legacy_roots` on a library (see [configuration](configuration.md#libraries)) to map the absolute paths stored in an old database onto the current root. Old `FPS` metadata is migrated to `frame_rate` when read (and written that way on the next save).

New records use schema version 3 with relative media paths. The first successful ingest, edit or metadata write into an old database writes `data.legacy.backup.json` beside it, then converts it. **Older TinyLib browsers cannot read schema version 3.** Deploy the new version to all users before enabling writes to a shared production library, or try a separate library first.

## Farm staging

Ingest jobs stage files in `<library>/.tinylib-staging/<job-id>`. A failed job keeps its staging folder for diagnosis and is not added to the library. When Deadline retries the job, on the same or another worker, the retry deletes that folder and starts over. If another worker still has files open in it, the retry stops instead. A folder that could not be fully deleted is left as `<job-id>.retry-<code>`; delete it by hand. See [deployment](deployment.md#deadline-104) for the farm setup.
