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
