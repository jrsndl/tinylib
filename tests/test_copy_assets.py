"""Copy Assets: destination folder, planning, copying and Nuke Read nodes."""
import datetime
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tinylib.actions import Action

ACTION = Path(__file__).resolve().parents[1] / 'actions' / 'copy_assets'


def load(name):
    key = '_tinylib_copy_assets_' + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, ACTION / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


plan_module = load('copy_plan')
reads = load('nuke_reads')
action = load('action')


class Base(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library = self.root / 'library'
        self.out = self.root / 'shot' / 'comp'
        self.out.mkdir(parents=True)

    def sequence(self, name, first, last, folder='mains'):
        for frame in range(first, last + 1):
            path = self.library / folder / ('%s.%04d.exr' % (name, frame))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'%s %d' % (name.encode(), frame))
        return str(self.library / folder / (name + '.####.exr'))

    def single(self, relative, data=b'x'):
        path = self.library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def footage(self, name, first=1001, last=1005, **values):
        asset = {'name': name, 'kind': 'footage', 'library_root': str(self.library),
                 'main': self.sequence(name, first, last), 'first': first, 'last': last, 'colorspace': 'ACEScg'}
        asset.update(values)
        return asset


class DestinationTests(Base):
    def test_template_relative_to_the_base_folder(self):
        day = datetime.date(2026, 10, 23)
        self.assertEqual(plan_module.destination_root(self.out, '.'), self.out)
        self.assertEqual(plan_module.destination_root(self.out, '../elements/{date}', today=day),
                         self.root / 'shot' / 'elements' / '20261023')
        self.assertEqual(plan_module.destination_root(self.out, 'lib/{collection}', 'fire: v2'), self.out / 'lib' / 'fire_ v2')
        self.assertEqual(plan_module.destination_root(self.out, ''), self.out)
        with patch.dict(os.environ, {'TL_TEST_SHOT': 'sh010'}):
            self.assertEqual(plan_module.destination_root(self.out, '$TL_TEST_SHOT'), self.out / 'sh010')

    def test_the_base_folder_must_be_an_existing_full_path(self):
        for base, message in (('', 'Choose'), ('relative/folder', 'full path'), (self.root / 'none', 'does not exist')):
            with self.assertRaisesRegex(ValueError, message):
                plan_module.destination_root(base, '.')


class PlanTests(Base):
    def test_sequences_in_subfolders_named_after_them(self):
        asset = self.footage('fire_v001')
        items, skipped = plan_module.plan([asset], 'main', self.out)
        self.assertEqual(skipped, [])
        files = items[0]['files']
        self.assertEqual(len(files), 5)
        self.assertEqual(files[0][1], self.out / 'fire_v001' / 'fire_v001.1001.exr')
        self.assertEqual(items[0]['read'], {'path': self.out / 'fire_v001' / 'fire_v001.####.exr', 'first': 1001, 'last': 1005})
        flat, _ = plan_module.plan([asset], 'main', self.out, subfolders=False)
        self.assertEqual(flat[0]['files'][0][1], self.out / 'fire_v001.1001.exr')

    def test_in_out_limits_the_frames(self):
        asset = self.footage('smoke')
        trims = {'smoke': (1002, 1003)}
        items, _ = plan_module.plan([asset], 'main', self.out, in_out=True, trim_for=lambda a: trims.get(a['name']))
        self.assertEqual([dst.name for _, dst in items[0]['files']], ['smoke.1002.exr', 'smoke.1003.exr'])
        self.assertEqual((items[0]['read']['first'], items[0]['read']['last']), (1002, 1003))
        whole, _ = plan_module.plan([asset], 'main', self.out, in_out=True, trim_for=lambda a: None)
        self.assertEqual(len(whole[0]['files']), 5)
        clipped, _ = plan_module.plan([asset], 'main', self.out, in_out=True, trim_for=lambda a: {'in': 990, 'out': 1001})
        self.assertEqual(len(clipped[0]['files']), 1)
        _, skipped = plan_module.plan([asset], 'main', self.out, in_out=True, trim_for=lambda a: (2000, 2010))
        self.assertIn('outside', skipped[0][1])

    def test_highres_proxy_and_missing_components(self):
        asset = self.footage('dust', highres=self.sequence('dust_4k', 1, 3, 'highres') + ' 1-3',
                             proxy=self.single('proxies/dust.mp4'))
        items, _ = plan_module.plan([asset], 'highres', self.out)
        self.assertEqual([dst.name for _, dst in items[0]['files']], ['dust_4k.0001.exr', 'dust_4k.0002.exr', 'dust_4k.0003.exr'])
        items, _ = plan_module.plan([asset], 'proxy', self.out)
        self.assertEqual(items[0]['files'], [(Path(asset['proxy']), self.out / 'dust.mp4')])
        self.assertEqual(items[0]['read'], {'path': self.out / 'dust.mp4', 'first': None, 'last': None})
        bare = self.footage('bare')
        items, skipped = plan_module.plan([bare], 'proxy', self.out)
        self.assertEqual((items, skipped), ([], [('bare', 'no proxy')]))
        with self.assertRaises(ValueError):
            plan_module.plan([asset], 'thumb', self.out)

    def test_stills_and_folders(self):
        still = {'name': 'sky', 'kind': 'still', 'main': self.single('stills/sky.jpg')}
        folder = self.library / 'kits' / 'trees'
        (folder / 'maps').mkdir(parents=True)
        (folder / 'tree.abc').write_bytes(b'a')
        (folder / 'maps' / 'bark.png').write_bytes(b'b')
        kit = {'name': 'trees', 'kind': 'folder', 'main': str(folder)}
        items, _ = plan_module.plan([still, kit], 'main', self.out)
        self.assertEqual(items[0]['files'], [(Path(still['main']), self.out / 'sky.jpg')])
        self.assertEqual(sorted(dst for _, dst in items[1]['files']),
                         [self.out / 'trees' / 'maps' / 'bark.png', self.out / 'trees' / 'tree.abc'])
        self.assertIsNone(items[1]['read'])

    def test_missing_files_clashes_and_self_copies_are_refused(self):
        asset = self.footage('gap')
        (self.library / 'mains' / 'gap.1003.exr').unlink()
        with self.assertRaisesRegex(ValueError, '1 main file'):
            plan_module.plan([asset], 'main', self.out)
        one = {'name': 'a', 'main': self.single('one/plate.exr')}
        two = {'name': 'b', 'main': self.single('two/plate.exr')}
        with self.assertRaisesRegex(ValueError, 'both be copied'):
            plan_module.plan([one, two], 'main', self.out)
        with self.assertRaisesRegex(ValueError, 'onto itself'):
            plan_module.plan([one], 'main', self.library / 'one')
        _, skipped = plan_module.plan([dict(asset, first=None, last=None)], 'main', self.out)
        self.assertIn('no frame range', skipped[0][1])


class CopyTests(Base):
    def test_copy_keeps_or_overwrites_existing_files_and_can_be_cancelled(self):
        asset = self.footage('fire', 1, 4)
        items, _ = plan_module.plan([asset], 'main', self.out)
        existing = self.out / 'fire' / 'fire.0002.exr'
        existing.parent.mkdir()
        existing.write_bytes(b'old')
        seen = []
        result = plan_module.run(items, progress=lambda done, total, path: seen.append((done, total)))
        self.assertEqual(result, {'copied': 3, 'existing': 1, 'cancelled': False})
        self.assertEqual(seen[-1], (4, 4))
        self.assertEqual(existing.read_bytes(), b'old')
        self.assertEqual((self.out / 'fire' / 'fire.0004.exr').read_bytes(), b'fire 4')
        self.assertEqual(plan_module.run(items, overwrite=True)['copied'], 4)
        self.assertEqual(existing.read_bytes(), b'fire 2')
        self.assertEqual([p.name for p in existing.parent.iterdir() if 'tinylib-copy' in p.name], [])
        calls = []
        cancelled = plan_module.run(items, overwrite=True, cancelled=lambda: calls.append(1) or len(calls) > 2)
        self.assertEqual(cancelled, {'copied': 2, 'existing': 0, 'cancelled': True})

    def test_a_failed_copy_leaves_no_partial_file(self):
        source = Path(self.single('a.exr', b'data'))
        target = self.out / 'a.exr'
        with patch.object(plan_module.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                plan_module.copy_file(source, target)
        self.assertEqual(list(self.out.iterdir()), [])


class ScopeTests(Base):
    def test_never_the_whole_main_view(self):
        assets = [self.footage('a'), self.footage('b')]
        with self.assertRaisesRegex(ValueError, 'selected assets only'):
            action.scope(assets, {'whole_view': True})
        self.assertEqual(action.scope(assets, {'whole_view': False}), assets)

    def test_collection_selection_or_whole_collection(self):
        assets = [self.footage('a'), self.footage('b')]
        context = {'collection': 'fx', 'collection_selected': [dict(assets[1])]}
        self.assertEqual(action.scope(assets, context), [assets[1]])
        self.assertEqual(action.scope(assets, {'collection': 'fx', 'collection_selected': []}), assets)

    def test_manifest_hides_it_from_the_whole_view(self):
        manifest = Action(ACTION)
        self.assertFalse(manifest.whole_view)
        self.assertEqual(manifest.manifest['hosts'], ['nuke', 'standalone'])


class Knob:
    def __init__(self, value=None, values=()):
        self.v, self.options = value, list(values)

    def setValue(self, value):
        self.v = value

    def value(self):
        return self.v

    def values(self):
        return self.options


class FakeRead(dict):
    def __init__(self):
        super().__init__({name: Knob() for name in ('file', 'first', 'origfirst', 'last', 'origlast',
                                                     'frame_mode', 'frame', 'label')})
        self['colorspace'] = Knob('default', ['default', 'ACES - ACEScg', 'sRGB'])


class NukeReadTests(Base):
    def fake_nuke(self):
        nuke = types.ModuleType('nuke')
        root = {'first_frame': Knob(1001), 'last_frame': Knob(1100)}

        class Root(dict):
            def name(self):
                return 'D:/shots/sh010/comp/sh010_v001.nk'
        nuke.root = lambda: Root(root)
        nuke.nodes = types.SimpleNamespace(Read=FakeRead)
        return nuke

    def test_read_nodes_frame_modes_and_colorspace(self):
        assets = [self.footage('fire'), {'name': 'sky', 'kind': 'still', 'main': self.single('sky.jpg'), 'colorspace': 'Linear Rec.709'}]
        items, _ = plan_module.plan(assets, 'main', self.out)
        with patch.dict(sys.modules, {'nuke': self.fake_nuke()}):
            self.assertEqual(reads.script_folder(), 'D:/shots/sh010/comp')
            nodes, warnings = reads.create_reads(items, 'main', 'start')
            self.assertEqual(nodes[0]['file'].value(), str(self.out / 'fire' / 'fire.####.exr').replace('\\', '/'))
            self.assertEqual((nodes[0]['first'].value(), nodes[0]['last'].value()), (1001, 1005))
            self.assertEqual(nodes[0]['colorspace'].value(), 'ACES - ACEScg')
            self.assertEqual((nodes[0]['frame_mode'].value(), nodes[0]['frame'].value()), ('start at', '1001'))
            self.assertIsNone(nodes[1]['first'].value())
            self.assertEqual(nodes[1]['colorspace'].value(), 'default')
            self.assertIn('Linear Rec.709', warnings[0])
            nodes, _ = reads.create_reads(items, 'proxy', 'expression')
            self.assertEqual((nodes[0]['frame_mode'].value(), nodes[0]['frame'].value()), ('expression', ''))
            self.assertEqual(nodes[0]['colorspace'].value(), 'default')
            nodes, _ = reads.create_reads(items, 'main')
            self.assertIsNone(nodes[0]['frame_mode'].value())


if __name__ == '__main__':
    unittest.main()
