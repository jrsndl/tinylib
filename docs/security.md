# Security setup

TinyLib keeps security simple. Windows file permissions protect a few files. TinyLib reads those files to decide what each artist may do in its interface.

## The three files

| File | Location | Artists | Admins | Purpose |
| --- | --- | --- | --- | --- |
| `tinylib.ini` | App folder, beside `menu.py` and `worker.py` | read | write | Points to the security file |
| `security.json` | Pipeline share | read | write | Users, groups, libraries and grants |
| `tinylib_data.json` + media | Each library root | read; write only where they may edit or ingest | write | The library itself |

`tinylib.ini`:

```ini
[tinylib]
security = //studio/pipeline/tinylib/security.json
config   = //studio/pipeline/tinylib/studio.json
```

Copy `tinylib.ini.example` to start. Relative paths resolve beside the ini. Environment variables are **not** expanded. Every key of the three files is listed in the [configuration reference](configuration.md).

## Rules

1. **Only `tinylib.ini` chooses the security file.** `TINYLIB_CONFIG`, `launch.py --config`, `*.local.json` overrides and the old `security_config` key can choose the *browser* configuration (tools, colour spaces, profiles, Deadline). They never choose the security file. TinyLib ignores `security_config` if it is still present.
2. **The security file lists the libraries.** A library that isn't registered in `security.json` gets no grants, except for admins. `read_only: true` in the security file wins over any browser configuration. A library must also be listed in the browser configuration to appear in the browser; the two entries are matched by root folder.
3. **TinyLib fails closed.** If `tinylib.ini` is missing or incomplete, or `security.json` is missing, unreadable, invalid or has no users, nobody gets access. The status line shows the reason. TinyLib never creates an administrator by itself.
4. **Windows decides who can change rights.** Saving in **Access rights…** requires membership of `admins` *and* Windows write permission on `security.json`. If Windows refuses the write, the dialog says so. Keep the `admins` group in step with whoever can write the file.
5. **Install the app on the share.** Artists must not be able to edit their own `tinylib.ini`. A local copy of the app folder defeats rule 1.

## First-time setup

1. Copy the app folder to the shared install location. Give artists read-only access and admins write access.
2. Create `security.json` on the pipeline share. Give artists read access and admins write access to it and its folder. TinyLib takes a short write lock (`.tinylib-write.lock`) in that folder while saving; a lock left by a crashed writer is removed automatically (see [write locks](admin.md#write-locks)).
3. Copy `tinylib.ini.example` to `tinylib.ini` and set `security` (and optionally `config`).
4. As an admin, from the app folder:

   ```powershell
   python -m tinylib.admin whoami                  # prints e.g. studio\jane
   python -m tinylib.admin add-user "studio\jane"  # creates security.json if needed
   python -m tinylib.admin show
   ```

5. Start TinyLib as that admin. Add libraries with **Library tools → Create New Library…**, which registers them in both the browser configuration and `security.json`. To add them by hand, add the same root to `libraries` in both files. Then add artists in **Access rights…** or with `add-user <account> users`.

`add-user` accepts groups after the account (`add-user "studio\joe" users restricted`). Without groups it adds the account to `admins`. `remove-user` refuses to remove the last admin.

## Library folders

TinyLib's groups only control what its own interface does. Anyone who can run Python in Nuke can write any file their Windows account may write. Real protection for a library therefore comes from Windows permissions on its root:

- **View-only for artists:** read-only access to the folder.
- **Artists edit or ingest:** write access for those artists.
- **Farm account:** write access to every library it publishes into.

TinyLib's groups then add the finer layer on top: which fields, which actions and which libraries appear.

## Farm

The Deadline worker reads the same `tinylib.ini`, so it uses the same security file. Do **not** make the farm account an admin. Add a dedicated group instead:

```json
"access": {
  "groups": ["admins", "managers", "users", "restricted", "farm"],
  "users": {"studio\\deadline": ["farm"]},
  "defaults": {"view": ["users", "managers", "admins", "farm"], "ingest": ["admins", "farm"]}
}
```

The worker takes tool paths (ffmpeg, ffprobe, oiiotool, vfx-transcode) from the `[tools]` section of **its own** `tinylib.ini`, and the processing profile from its own configuration. It never uses the values in the job manifest, because anyone who can write to the Deadline spool folder can edit a manifest. Keep the spool writable only by artists who may ingest and by the farm.

The worker checks *its own* account's grants. It cannot verify which artist submitted a job, so `ingest_user` in a manifest is informational.

## What this does not protect against

A determined artist with write access to a library can still change its files directly, outside TinyLib. The design aims to prevent accidental and casual misuse, and to keep rights in one admin-controlled file. Windows permissions on library roots are the hard boundary.
