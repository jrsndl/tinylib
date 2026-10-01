"""Versioned asset-type, representation, extension-group and metadata schema."""
import copy
import re
from pathlib import Path

SCHEMA_VERSION = 1

DEFAULT_EXTENSION_GROUPS = {
    'extensions_stills': ['exr', 'bmp', 'jpg', 'jpeg', 'tif', 'tiff', 'png', 'tga', 'dpx', 'cin', 'psd', 'tx', 'sgi', 'heic', 'fla', 'cr2', 'cr3', 'dng', 'webp'],
    'extensions_stills_raw': ['cr2', 'cr3', 'dng', 'nef', 'nrw', 'arw', 'srf', 'sr2'],
    'extensions_footage_containers': ['mov', 'mp4', 'webm', 'avi', 'qt', 'wmv', 'mpg', 'mxf', 'mkv'],
    'extensions_models': ['abc', 'fbx', 'obj', 'usd', 'usda', 'usdc', 'usdz', 'gltf', 'glb', 'ply', 'stl'],
    'extensions_3dscene': ['3ds', 'blend', 'c4d', 'ma', 'max', 'mb', 'hip', 'hipnc', 'hiplc'],
    'extensions_splats': ['ply', 'spz', 'sog', 'splat', 'ksplat'],
    'extensions_materials': ['mtlx', 'sbs', 'sbsar', 'mra'],
    'extensions_workfiles': ['nk', 'aep', 'ai', 'indd', 'kra', 'pur', 'sni'],
}

VISUAL_METADATA = {
    'width': 'integer', 'height': 'integer', 'pixel_aspect': 'number', 'colorspace': 'text',
    'is_equirectangular': 'boolean', 'is_raw': 'boolean', 'timecode': 'text',
    'frame_rate': 'number', 'reelid': 'text', 'file_size_main': 'integer', 'file_size_total': 'integer',
}

ASSET_TYPES = {
    'still': {
        'label': 'Still',
        'representations': {
            'main': (True, ['extensions_stills', 'extensions_stills_raw']),
            'highres': (False, ['extensions_stills', 'extensions_stills_raw']),
            'proxy': (False, ['jpg']), 'thumb': (True, ['jpg']),
        },
        'metadata': VISUAL_METADATA,
    },
    'footage': {
        'label': 'Footage',
        'representations': {
            'main': (True, ['extensions_stills', 'extensions_stills_raw', 'extensions_footage_containers']),
            'highres': (False, ['extensions_stills', 'extensions_stills_raw', 'extensions_footage_containers']),
            'proxy': (False, ['mp4']), 'thumb': (True, ['jpg']), 'filmstrip': (False, ['jpg']),
        },
        'metadata': dict(VISUAL_METADATA, duration_frames='integer', duration_seconds='number',
                         frame_start='integer', frame_end='integer'),
    },
    'model': {
        'label': 'Model',
        'representations': {
            'main': (True, ['extensions_models']), 'highres': (False, ['extensions_models']),
            'proxy': (False, ['extensions_models']), 'thumb': (True, ['jpg']),
            'scene': (False, ['extensions_3dscene']),
        },
        'metadata': {'faces': 'integer', 'is_uvmapped': 'boolean', 'is_textured': 'boolean',
                     'textures': 'text_list', 'is_rigged': 'boolean', 'is_animated': 'boolean',
                     'source_dcc': 'text', 'renderer': 'text', 'file_size_main': 'integer',
                     'file_size_total': 'integer'},
    },
    'folder': {
        'label': 'Folder',
        'representations': {'main': (True, ['*']), 'highres': (False, ['*']),
                            'proxy': (False, ['*']), 'thumb': (True, ['jpg'])},
        'metadata': {'files': 'text_list', 'file_size_main': 'integer', 'file_size_total': 'integer'},
    },
    'splat': {
        'label': 'Splat',
        'representations': {'main': (True, ['extensions_splats']), 'thumb': (True, ['jpg'])},
        'metadata': {'image_number': 'integer', 'file_size_main': 'integer', 'file_size_total': 'integer'},
    },
    'pdf': {
        'label': 'PDF',
        'representations': {'main': (True, ['pdf']), 'thumb': (True, ['jpg'])},
        'metadata': {'file_size_main': 'integer', 'file_size_total': 'integer'},
    },
    'material': {
        'label': 'Material',
        'representations': {'main': (True, ['extensions_materials']), 'thumb': (True, ['jpg'])},
        'metadata': {'renderer': 'text', 'file_size_main': 'integer', 'file_size_total': 'integer'},
    },
}

def extension_groups(settings=None):
    groups = copy.deepcopy(DEFAULT_EXTENSION_GROUPS)
    configured = (settings or {}).get('extension_groups', {})
    if not isinstance(configured, dict):
        raise ValueError('extension_groups must be an object.')
    for name, values in configured.items():
        if name not in groups or not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError('Invalid extension group: ' + str(name))
        groups[name] = list(dict.fromkeys(value.lower().lstrip('.') for value in values if value.strip()))
    return groups


def canonical_type(kind):
    return str(kind).strip().lower()


def _split_sequence(value):
    match = re.match(r'^(.*?)\s+(-?\d+)-(-?\d+)$', str(value))
    return (match[1], int(match[2]), int(match[3])) if match else (str(value), None, None)


def definition(kind):
    name = canonical_type(kind)
    if name not in ASSET_TYPES:
        raise ValueError('Unknown asset type: ' + str(kind))
    return ASSET_TYPES[name]


def representation(kind, role, settings=None):
    role = 'thumb' if role == 'thumbnail' else role
    item = definition(kind)['representations'].get(role)
    if not item:
        raise ValueError('%s does not support %s.' % (definition(kind)['label'], role))
    required, sources = item
    groups = extension_groups(settings)
    extensions = set()
    any_path = False
    for source in sources:
        if source == '*':
            any_path = True
        elif source in groups:
            extensions.update(groups[source])
        else:
            extensions.add(source.lower().lstrip('.'))
    return {'required': required, 'extensions': extensions, 'any': any_path}


def validate_representation(kind, role, value, settings=None, exists=True):
    spec = representation(kind, role, settings)
    if not value:
        if spec['required']:
            raise ValueError('%s is required for %s.' % (role, definition(kind)['label']))
        return ''
    pattern, first, last = _split_sequence(value)
    path = Path(pattern)
    sequence = bool(re.search(r'#+|%0?\d*d', pattern))
    if exists and sequence:
        from .library import sequence_files
        sequence_files(value)
    elif exists and not path.exists():
        raise ValueError('%s does not exist: %s' % (role, pattern))
    if canonical_type(kind) == 'folder' and role != 'thumb':
        if exists and not path.is_dir():
            raise ValueError('%s must be a folder.' % role)
    elif not spec['any'] and path.suffix.lower().lstrip('.') not in spec['extensions']:
        raise ValueError('%s for %s must use: %s' % (role, definition(kind)['label'], ', '.join(sorted(spec['extensions']))))
    return value


def detect_type(value, settings=None, is_sequence=False):
    path = Path(_split_sequence(value)[0])
    if path.is_dir():
        return 'folder'
    extension = path.suffix.lower().lstrip('.')
    if is_sequence or extension in extension_groups(settings)['extensions_footage_containers']:
        return 'footage'
    matches = [kind for kind in ('still', 'model', 'splat', 'pdf', 'material')
               if extension in representation(kind, 'main', settings)['extensions']]
    if len(matches) > 1:
        raise ValueError('.%s is valid for multiple asset types (%s); choose the type explicitly.' %
                         (extension, ', '.join(matches)))
    if matches:
        return matches[0]
    raise ValueError('Cannot detect an asset type for .' + extension)


def default_metadata(kind):
    defaults = {'integer': 0, 'number': 0.0, 'boolean': False, 'text': '', 'text_list': []}
    return {name: copy.deepcopy(defaults[value_type]) for name, value_type in definition(kind)['metadata'].items()}


def validate_metadata(kind, metadata, complete=False):
    if not isinstance(metadata, dict):
        raise ValueError('metadata must be an object.')
    fields = definition(kind)['metadata']
    if complete and set(fields) - set(metadata):
        raise ValueError('Missing metadata: ' + ', '.join(sorted(set(fields) - set(metadata))))
    for name, value in metadata.items():
        if name not in fields:
            continue  # Preserve studio-specific and legacy probe fields.
        expected = fields[name]
        valid = ((expected == 'boolean' and type(value) is bool) or
                 (expected == 'integer' and type(value) is int and value >= 0) or
                 (expected == 'number' and type(value) in (int, float) and value >= 0) or
                 (expected == 'text' and isinstance(value, str)) or
                 (expected == 'text_list' and isinstance(value, list) and all(isinstance(item, str) for item in value)))
        if not valid:
            raise ValueError('%s metadata must be %s.' % (name, expected.replace('_', ' ')))
    return metadata


def relative_paths(values):
    for value in values:
        text = str(value).replace('\\', '/')
        if re.match(r'^[A-Za-z]:/|^/', text) or '..' in Path(text).parts:
            raise ValueError('Stored metadata paths must be relative to the asset folder: ' + text)
    return values
