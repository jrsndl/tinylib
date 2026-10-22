# Asset types and metadata

TinyLib's versioned asset schema is defined in `tinylib/asset_types.py`. Extension groups are copied into the loaded studio settings and may be overridden with `extension_groups` in the browser configuration (see [configuration](configuration.md#extension_groups)); a group given there replaces that group's built-in list. Values omit the leading dot and comparisons are case-insensitive. Each asset has a required `main` representation and a required JPEG `thumb` representation. Optional representations may be omitted.

| Type | Main | Optional representations | Metadata |
| --- | --- | --- | --- |
| Still | still or camera-raw image (EXR or HDR when lat-long) | highres image, JPEG proxy | width, height, pixel aspect, equirectangular/raw flags, timecode, frame rate, reel ID, sizes. The color space is the asset's own **Color space** field, not metadata. |
| Footage | image sequence, camera-raw sequence or footage container | highres, MP4 proxy, JPEG filmstrip | Still fields plus frame duration, seconds and frame range |
| Model | model file | highres model, proxy model, scene | faces, UV/textured/rigged/animated flags, relative texture paths, source DCC, renderer, sizes |
| Folder | folder containing any files | highres folder, proxy folder | relative file list and sizes |
| Splat | splat file | none | source image count and sizes |
| PDF | PDF file | none | sizes |
| Material | material file | none | target renderer and sizes |

A Still with `is_equirectangular` true is a lat-long (HDRI) still. Its main must be EXR or HDR (`hdr` is in `extensions_stills`). Its proxy and thumbnail are tonemapped 2:1 lat-long JPEGs of any size; other Stills keep the 960 × 506 thumbnail, and a Still's proxy is never size-checked. Lat-long stills open in the 360 viewer.

Model texture paths and Folder file paths are stored relative to the asset folder. Absolute paths and parent traversal are rejected. File sizes are bytes. Integer and numeric metadata cannot be negative.

The predefined groups are `extensions_stills`, `extensions_stills_raw`, `extensions_footage_containers`, `extensions_models`, `extensions_3dscene`, `extensions_splats`, `extensions_materials`, and `extensions_workfiles`. The public `config/studio.json` contains their complete defaults.

Generated previews are available for Still and Footage. Models, Folders, Splats, PDFs and Materials require a supplied JPEG thumbnail. Main and highres data are copied unchanged. Metadata extracted by the worker replaces placeholder values in the manifest; fields unavailable to the worker remain user-supplied.

## Common asset fields

These top-level fields apply to every asset type. Existing `category` remains the primary category (category 0); it is not renamed. Missing values are filled when loading, and written on the next database save. Loading alone does not rewrite a library.

| Fields | Type | Default |
| --- | --- | --- |
| label | string, one line, up to 200 characters | absent or empty: the name is shown |
| category_1, category_2, category_3 | string | empty |
| version_to_delete | boolean | false |
| version_number | integer | 1 |
| version_superseded_by_id | string, replacement asset ID | empty |
| ingest_user | string | unknown |
| ingest_date | string, YYYYMMDD | empty (unknown) |
| ingest_note | multiline string | empty |
| acquire_project, acquire_date, acquire_source, acquire_author | string | empty |
| acquire_price | currency code, space, amount | eur 0 |
| acquire_usage, acquire_note | multiline string | empty |

`label` is shown in the interface instead of `name`; `name` stays the name used at ingest and the folder name. Older assets have no `label` until one is set. New ingests set `ingest_user` to the submitting Windows identity and `ingest_date` to the submission date. Extra categories are labels; only the primary category determines the folder. The Properties panel offers multiline editors for notes/usage, a checkbox for deletion, and an integer version control. Field visibility and editing follow the studio/library prefix ACLs described in [actions and access](actions-and-access.md).
