# Processing audit

Inspected the four supplied `oldscripts/*.py` scripts and the `.bat` presets and `--help` output under `<transcoder installation>`.

The old thumbnail script chooses the largest compressed source frame, which is a useful approximation for active content in smoke/fire sequences. It uses an ACES 1.2 OCIO conversion from `ACES - ACEScg` to `Output - Rec.709`. Its executable and OCIO paths refer to machine-specific locations. It sets a 960×540 variable but the active OIIO command only resizes to width 960; it does not guarantee the requested 960×506 thumbnail canvas.

The old proxy script skips stills and assigns 24 fps to every image sequence. The filmstrip variants differ: the batch version requests 12 frames and uses gamma 2.2; the non-batch version requests 24 and has an `apply_trc` argument without its leading hyphen. The proxy and filmstrip conversions do not share the thumbnail's OCIO transform. The code also uses shell-built command strings, `eval` for frame-rate output and a helper that can return success after failed intermediate rendering.

The replacement uses argument lists without a shell, checked subprocess return codes, rational FPS parsing and explicit color conversion. It preserves the main files. Every generated main-derived preview passes through the selected OCIO transform. Proxy-derived previews skip OCIO. A short image sequence is used to build filmstrips with FFmpeg's tile filter, avoiding the old long command line containing 24 separate input paths.

Locally available tools:

- `executables/oiio2/oiiotool.exe`: OpenImageIO 3.0.6.1.
- `executables/ffmpeg/ffmpeg.exe` and `ffprobe.exe`: bundled FFmpeg build dated 2026-09-17.
- `vfx-transcode.exe`: reports version September 2024; accepts explicit input/output, OCIO, tools, encoding and filtering arguments. Its FPS argument requires an integer or fraction, not a decimal such as `24.0`; the integration converts FPS accordingly.
- `<OCIO config>`: actual ACES 1.2 config used by the old presets.

No binaries or old scripts were edited. The optional VFX Transcode backend runs on the staged main sequence, so its temporary files cannot be written into the source asset.

Reference documentation: [OpenImageIO fit and color conversion](https://openimageio.readthedocs.io/en/v3.1.12.1/oiiotool.html) and [Deadline Python plugin](https://docs.thinkboxsoftware.com/products/deadline/10.3/1_User%20Manual/manual/app-python.html).
