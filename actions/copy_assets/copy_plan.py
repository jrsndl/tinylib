"""Copy Assets: which files go where, and the copying itself. No Qt here.

Everything is planned before anything is copied: every source file must exist and no two
copies may land on the same destination file. Each file is copied to a temporary name
beside its destination and then renamed into place, so a cancelled or failed copy never
leaves a partial file under the real name.
"""
import datetime
import os
import re
import shutil
import uuid
from pathlib import Path

from tinylib.asset_fields import display_name
from tinylib.custom_thumbnail import replace_file
from tinylib.ingest import clean_file_name
from tinylib.library import frame_path, split_sequence
from tinylib.storage import clear_hidden
from tinylib.trim import clean_trim

COMPONENTS = ('main', 'highres', 'proxy')
SEQUENCE_TOKEN = re.compile(r'#+|%0?\d*d')


def destination_root(base, template, collection='', today=None):
    """The folder all assets are copied into: ``template`` (``{date}``, ``{collection}``, ``..``
    and environment variables allowed) relative to ``base``; an absolute template is used as is."""
    base = str(base or '').strip()
    if not base:
        raise ValueError('Choose the base folder.')
    if not Path(base).is_absolute():
        raise ValueError('The base folder must be a full path: %s' % base)
    if not Path(base).is_dir():
        raise ValueError('The base folder does not exist: %s' % base)
    text = os.path.expandvars(str(template or '').strip()).format(
        date=(today or datetime.date.today()).strftime('%Y%m%d'),
        collection=re.sub(r'[<>:"/\\|?*]+', '_', collection or '').strip() or 'collection')
    return Path(os.path.normpath(os.path.join(base, text or '.')))


def component_source(asset, component):
    """(pattern, first, last) of an asset's component, or None when it has none."""
    if component not in COMPONENTS:
        raise ValueError('Unknown component %r.' % component)
    value = asset.get(component)
    if not value:
        return None
    pattern, first, last = split_sequence(value)
    if first is None and component == 'main':
        first, last = asset.get('first'), asset.get('last')
    elif first is None and component == 'highres':
        first, last = asset.get('highres_first'), asset.get('highres_last')
    if not SEQUENCE_TOKEN.search(Path(pattern).name):
        first = last = None
    return pattern, first, last


def plan(assets, component, root, subfolders=True, in_out=False, trim_for=None, check=True):
    """``(items, skipped)``. Each item: ``{'asset', 'files': [(source, destination)], 'read':
    {'path', 'first', 'last'}}``; skipped: ``[(asset name, reason)]``. Raises when a source file
    is missing or two copies would write the same destination file."""
    root = Path(root)
    items, skipped, targets = [], [], {}
    for asset in assets:
        name = display_name(asset)
        source = component_source(asset, component)
        if source is None:
            skipped.append((name, 'no %s' % component))
            continue
        pattern, first, last = source
        path = Path(pattern)
        if SEQUENCE_TOKEN.search(path.name):
            if first is None or last is None or last < first:
                skipped.append((name, 'its %s has no frame range' % component))
                continue
            low, high = first, last
            if in_out:
                trim = clean_trim(trim_for(asset)) if trim_for else None
                if trim:
                    low, high = max(first, trim[0]), min(last, trim[1])
                    if low > high:
                        skipped.append((name, 'its in/out points are outside the %s frames' % component))
                        continue
            folder = root / clean_file_name(str(pattern)) if subfolders else root
            files = [(Path(frame_path(pattern, frame)), folder / Path(frame_path(pattern, frame)).name)
                     for frame in range(low, high + 1)]
            read = {'path': folder / path.name, 'first': low, 'last': high}
        elif check and path.is_dir():
            files = [(item, root / path.name / item.relative_to(path)) for item in sorted(path.rglob('*')) if item.is_file()]
            read = None
        else:
            files = [(path, root / path.name)]
            read = {'path': root / path.name, 'first': None, 'last': None}
        if check:
            missing = [str(src) for src, _ in files if not src.is_file()]
            if missing:
                raise ValueError('%s: %d %s file(s) are missing, e.g. %s' % (name, len(missing), component, missing[0]))
        for src, destination in files:
            key = os.path.normcase(os.path.normpath(str(destination)))
            if key == os.path.normcase(os.path.normpath(str(src))):
                raise ValueError('%s would be copied onto itself (%s). Choose another destination.' % (name, src))
            if key in targets and targets[key] != name:
                raise ValueError('%s and %s would both be copied to %s. Use a subfolder per sequence, or copy them '
                                 'separately.' % (targets[key], name, destination))
            targets[key] = name
        items.append({'asset': asset, 'files': files, 'read': read})
    return items, skipped


def total_files(items):
    return sum(len(item['files']) for item in items)


def copy_file(source, destination):
    """Copy to a temporary name beside ``destination``, then rename it into place.

    The temporary name never starts with a dot: Samba shares hide such files, and the file
    would stay hidden after the rename."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('%s.%s.tinylib-copy' % (destination.name, uuid.uuid4().hex[:8]))
    try:
        shutil.copy2(str(source), str(temporary))
        replace_file(temporary, destination)  # retried while the share still holds the new file
        clear_hidden(destination)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def run(items, overwrite=False, progress=None, cancelled=None):
    """Copy the planned files. Returns ``{'copied', 'existing', 'cancelled'}``; existing files are
    kept unless ``overwrite``. ``progress(done, total, path)`` reports each file."""
    total = total_files(items)
    done = copied = existing = 0
    for item in items:
        for source, destination in item['files']:
            if cancelled and cancelled():
                return {'copied': copied, 'existing': existing, 'cancelled': True}
            if destination.exists() and not overwrite:
                existing += 1
            else:
                copy_file(source, destination)
                copied += 1
            done += 1
            if progress:
                progress(done, total, str(destination))
    return {'copied': copied, 'existing': existing, 'cancelled': False}
