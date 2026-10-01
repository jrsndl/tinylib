# Asset types and metadata

TinyLib's versioned asset schema is defined in `tinylib/asset_types.py`. Extension groups are copied into the loaded studio settings and may be overridden in `config/studio.local.json`. Values omit the leading dot and comparisons are case-insensitive. Each asset has a required `main` representation and a required JPEG `thumb` representation. Optional representations may be omitted.

| Type | Main | Optional representations | Metadata |
| --- | --- | --- | --- |
| Still | still or camera-raw image | highres image, JPEG proxy | width, height, pixel aspect, colorspace, equirectangular/raw flags, timecode, frame rate, reel ID, sizes |
| Footage | image sequence, camera-raw sequence or footage container | highres, MP4 proxy, JPEG filmstrip | Still fields plus frame duration, seconds and frame range |
| Model | model file | highres model, proxy model, scene | faces, UV/textured/rigged/animated flags, relative texture paths, source DCC, renderer, sizes |
| Folder | folder containing any files | highres folder, proxy folder | relative file list and sizes |
| Splat | splat file | none | source image count and sizes |
| PDF | PDF file | none | sizes |
| Material | material file | none | target renderer and sizes |

Model texture paths and Folder file paths are stored relative to the asset folder. Absolute paths and parent traversal are rejected. File sizes are bytes. Integer and numeric metadata cannot be negative.

The predefined groups are `extensions_stills`, `extensions_stills_raw`, `extensions_footage_containers`, `extensions_models`, `extensions_3dscene`, `extensions_splats`, `extensions_materials`, and `extensions_workfiles`. The public `config/studio.json` contains their complete defaults.

Generated previews are available for Still and Footage. Models, Folders, Splats, PDFs and Materials require a supplied JPEG thumbnail. Main and highres data are copied unchanged. Metadata extracted by the worker replaces placeholder values in the manifest; fields unavailable to the worker remain user-supplied.
