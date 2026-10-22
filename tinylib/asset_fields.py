"""Common database fields shared by every asset type."""
import copy
import datetime
import re

DEFAULT_FIELDS = {
    'category_1': '', 'category_2': '', 'category_3': '',
    'version_to_delete': False, 'version_number': 1, 'version_superseded_by_id': '',
    'ingest_user': 'unknown', 'ingest_date': '', 'ingest_note': '',
    'acquire_project': '', 'acquire_date': '', 'acquire_source': '',
    'acquire_price': 'eur 0', 'acquire_author': '', 'acquire_usage': '', 'acquire_note': '',
}
MULTILINE_FIELDS = {'ingest_note', 'acquire_usage', 'acquire_note'}
LABEL_MAX = 200


def display_name(asset):
    """What the interface shows for an asset: its label, or its name when there is none.

    ``name`` stays the name used at ingest, which also names the asset's folder.
    Older assets have no ``label``; an empty one means the same.
    """
    label = asset.get('label')
    return label.strip() if isinstance(label, str) and label.strip() else asset.get('name', '')


PART_ROLES_HIDDEN = ('thumb', 'filmstrip')


def asset_parts(asset):
    """The asset's components and their file types, e.g. ``main:exr highres:exr proxy:jpg``.

    ``main`` comes first, the others alphabetically; thumbnail and filmstrip are left out.
    A component without a file extension (a Folder asset's folders) shows ``folder``.
    """
    roles = ('main', 'highres', 'proxy', 'scene', 'thumb', 'filmstrip')
    present = [role for role in roles if role not in PART_ROLES_HIDDEN and asset.get(role)]
    present.sort(key=lambda role: (role != 'main', role))
    parts = []
    for role in present:
        # A sequence may carry its frame range: "plate.####.exr 1001-1100".
        path = re.sub(r'\s+-?\d+--?\d+$', '', str(asset[role]).strip())
        name = re.split(r'[\\/]', path.rstrip('\\/'))[-1]
        extension = name.rsplit('.', 1)[1].lower() if '.' in name.strip('.') else 'folder'
        parts.append('%s:%s' % (role, extension))
    return ' '.join(parts)


def validate_label(value):
    if not isinstance(value, str):
        raise ValueError('The label must be text.')
    if re.search(r'[\r\n\t]', value):
        raise ValueError('The label must be one line.')
    if len(value) > LABEL_MAX:
        raise ValueError('The label can be at most %d characters.' % LABEL_MAX)


def apply_defaults(record):
    for key, value in DEFAULT_FIELDS.items():
        record.setdefault(key, copy.deepcopy(value))
    return record


def field_capability(key):
    return next((prefix + '_fields' for prefix in ('version', 'ingest', 'acquire')
                 if key.startswith(prefix)), None)


def validate_fields(record):
    if 'label' in record:
        validate_label(record['label'])
    for key, default in DEFAULT_FIELDS.items():
        if key not in record:
            continue
        value = record[key]
        if type(value) is not type(default):
            raise ValueError(key + ' has an invalid value type.')
        if key == 'version_number' and value < 1:
            raise ValueError('Version number must be at least 1.')
        if key == 'ingest_date' and value:
            if not re.fullmatch(r'\d{8}', value):
                raise ValueError(key + ' must use YYYYMMDD.')
            try:
                datetime.datetime.strptime(value, '%Y%m%d')
            except ValueError:
                raise ValueError(key + ' must be a valid date.')
        if key == 'acquire_price' and not re.fullmatch(r'[a-zA-Z]{3} \d+(?:\.\d+)?', value):
            raise ValueError('Acquire price must use a three-letter currency and amount, e.g. eur 0.')
