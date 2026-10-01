import tempfile
import unittest
from pathlib import Path

from tinylib.asset_types import (ASSET_TYPES, DEFAULT_EXTENSION_GROUPS, detect_type,
                                 extension_groups, relative_paths, representation,
                                 validate_metadata, validate_representation)
from tinylib.ingest import clean_asset_name, keywords_from_name, make_manifest, prepare_source
from tinylib.access import AccessControl
from tinylib.library import atomic_json
from tinylib.processing import process


class AssetTypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = {
            'libraries': [], 'profiles': {'preview': {}}, 'tools': {},
            '_config_path': str(self.root / 'studio.json')
        }
        self.settings['_access'] = AccessControl(self.settings, identity='test', persist=False)
        self.library = {'name': 'Test', 'root': str(self.root)}

    def file(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_schema_has_every_type_and_extension_group(self):
        self.assertEqual(set(ASSET_TYPES), {'still', 'footage', 'model', 'folder', 'splat', 'pdf', 'material'})
        self.assertEqual(set(extension_groups()), set(DEFAULT_EXTENSION_GROUPS))
        self.assertIn('nef', representation('still', 'main')['extensions'])
        self.assertEqual(representation('footage', 'proxy')['extensions'], {'mp4'})
        self.assertTrue(representation('folder', 'main')['any'])
        self.assertTrue(representation('material', 'thumb')['required'])

    def test_type_detection_and_explicit_sequence_validation(self):
        self.assertEqual(detect_type(self.file('plate.exr')), 'still')
        self.assertEqual(detect_type(self.file('clip.mxf')), 'footage')
        self.assertEqual(detect_type(self.file('model.usdc')), 'model')
        self.assertEqual(detect_type(self.file('paper.pdf')), 'pdf')
        self.assertEqual(detect_type(self.file('look.mtlx')), 'material')
        with self.assertRaises(ValueError):
            detect_type(self.file('ambiguous.ply'))
        self.file('seq.1001.exr'); self.file('seq.1002.exr')
        pattern = str(self.root / 'seq.####.exr') + ' 1001-1002'
        self.assertEqual(validate_representation('footage', 'main', pattern), pattern)

    def test_browsed_footage_source_name_and_keywords(self):
        for frame in (1001, 1002, 1003):
            self.file('BlueSky.Fire_v012-%04d.exr' % frame)
        source = prepare_source(str(self.root / 'BlueSky.Fire_v012-1001.exr'), 'footage', self.settings)
        self.assertTrue(source.endswith('BlueSky.Fire_v012-####.exr 1001-1003'))
        clean = clean_asset_name(source)
        self.assertEqual(clean, 'BlueSky.Fire')
        self.assertEqual(keywords_from_name(clean), ['blue', 'sky', 'fire'])
        self.assertEqual(prepare_source(str(self.root), 'footage', self.settings), source)
        with self.assertRaises(ValueError):
            prepare_source(self.file('unsupported.txt'), 'footage', self.settings)

    def test_metadata_types_and_relative_paths(self):
        validate_metadata('model', {'faces': 12, 'is_rigged': False, 'textures': ['textures/albedo.exr']})
        with self.assertRaises(ValueError):
            validate_metadata('model', {'faces': 1.5})
        with self.assertRaises(ValueError):
            relative_paths(['C:/absolute/file.exr'])
        with self.assertRaises(ValueError):
            relative_paths(['../outside.exr'])

    def test_nonvisual_manifest_requires_supplied_thumbnail(self):
        source = self.file('tree.fbx')
        thumb = self.file('tree.jpg')
        metadata = {'faces': 123, 'is_uvmapped': True, 'is_textured': False,
                    'textures': [], 'is_rigged': False, 'is_animated': False,
                    'source_dcc': 'Maya 2023', 'renderer': ''}
        job = make_manifest(self.settings, self.library, 'Tree', 'models', str(source), [], '',
                            'preview', {'thumb': {'mode': 'supply', 'path': str(thumb)}},
                            kind='model', metadata=metadata)
        self.assertEqual(job['kind'], 'model')
        self.assertEqual(job['metadata']['faces'], 123)
        with self.assertRaises(ValueError):
            make_manifest(self.settings, self.library, 'Tree2', 'models', str(source), [], '',
                          'preview', {'thumb': {'mode': 'generate', 'path': ''}}, kind='model')

    def test_nonvisual_worker_publishes_without_media_probe(self):
        source = self.file('rock.obj')
        source.write_bytes(b'model-data')
        thumb = self.file('rock.jpg')
        thumb.write_bytes(b'jpeg-data')
        job = make_manifest(self.settings, self.library, 'Rock', 'models', str(source), ['stone'], '',
                            'preview', {'thumb': {'mode': 'supply', 'path': str(thumb)}}, kind='model')
        path = self.root / 'job.json'
        atomic_json(path, job)
        record = process(path, access=self.settings['_access'])
        self.assertEqual(record['kind'], 'model')
        self.assertEqual(record['metadata']['file_size_main'], len(b'model-data'))
        self.assertTrue((self.root / 'models/Rock/main/rock.obj').is_file())
        self.assertTrue((self.root / 'models/Rock/thumb/Rock.jpg').is_file())

    def test_folder_worker_copies_tree_and_records_relative_files(self):
        source = self.root / 'source-folder'
        (source / 'textures').mkdir(parents=True)
        (source / 'readme.txt').write_text('hello')
        (source / 'textures/albedo.tx').write_bytes(b'texture')
        thumb = self.file('folder.jpg')
        job = make_manifest(self.settings, self.library, 'Package', 'folders', str(source), [], '',
                            'preview', {'thumb': {'mode': 'supply', 'path': str(thumb)}}, kind='folder')
        path = self.root / 'folder-job.json'
        atomic_json(path, job)
        record = process(path, access=self.settings['_access'])
        self.assertEqual(record['metadata']['files'], ['main/readme.txt', 'main/textures/albedo.tx'])
        self.assertEqual(Path(record['main']), self.root / 'folders/Package/main')
        self.assertTrue((self.root / 'folders/Package/main/textures/albedo.tx').is_file())

    def test_no_hdri_type_alias(self):
        with self.assertRaises(ValueError):
            representation('hdri', 'main')


if __name__ == '__main__':
    unittest.main()
