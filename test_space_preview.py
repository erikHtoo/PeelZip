import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import space_preview as preview
import ntfs_reclaim


class SpacePreviewTests(unittest.TestCase):
    def test_order_and_reclaim_timing(self):
        groups = [(70, 50, True)]
        self.assertEqual(preview.model(groups, reserve=0)['expected_extra'], 20)
        self.assertEqual(preview.model(groups, reserve=0)['cautious_extra'], 70)
        self.assertEqual(preview.model([(70, 50, False)], reserve=0)['expected_extra'], 70)
        self.assertEqual(preview.model(groups, same_disk=False, reserve=0)['expected_extra'], 70)
        # Large early expansion needs more space than final net growth alone.
        result = preview.model([(100, 10, True), (10, 100, True)], reserve=0)
        self.assertEqual(result['expected_extra'], 90)
        self.assertEqual(result['final_growth'], 0)

    def test_zip_is_read_only_and_follows_reverse_order(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root/'sample.zip'
            with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr('first', os.urandom(2*preview.MIB))
                z.writestr('last', b'a' * (3*preview.MIB))
            before = source.read_bytes()
            with patch.object(ntfs_reclaim, 'reclaim_range', side_effect=AssertionError('preview mutated source')):
                result = preview.estimate(source, root/'missing'/'out')
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((root/'missing').exists())
            self.assertEqual(result['ordinary_extra'], 5*preview.MIB)
            self.assertEqual(result['streaming_groups'], 2)
            self.assertLessEqual(result['expected_extra'], result['cautious_extra'])
            with patch.object(preview, 'model', wraps=preview.model) as model:
                preview.estimate(source, root/'out')
            self.assertEqual(model.call_args.args[0][0][0], 3*preview.MIB)

    def test_existing_destination_and_sparse_source_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root/'sample.zip'
            with zipfile.ZipFile(source, 'w') as z:
                z.writestr('file', bytes(preview.MIB))
            with self.assertRaisesRegex(ValueError, 'fresh extraction'):
                preview.estimate(source, root)
            with patch.object(ntfs_reclaim, 'allocated_bytes', return_value=0):
                with self.assertRaisesRegex(ValueError, 'already reclaimed'):
                    preview.estimate(source, root/'out')

    def test_native_and_split_metadata_only(self):
        decoder = Path(__file__).parent/'tools'/'7zz.exe'
        rar = Path('C:/Program Files/WinRAR/Rar.exe')
        cases = ['7z', '7z-hidden', 'zip-split']
        if rar.exists():
            cases.append('rar-split')
        for kind in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                payload = root/'file.bin'
                payload.write_bytes(os.urandom(2*preview.MIB))
                if kind == 'rar-split':
                    cmd = [str(rar), 'a', '-ep1', '-v1m', str(root/'sample.rar'), str(payload)]
                    source = root/'sample.part1.rar'
                else:
                    fmt = 'zip' if kind == 'zip-split' else '7z'
                    cmd = [str(decoder), 'a', '-mx=1']
                    if kind == '7z-hidden':
                        cmd += ['-psecret', '-mhe=on']
                    if kind == 'zip-split':
                        cmd += ['-psecret', '-v1m']
                    cmd += [str(root/f'sample.{fmt}'), str(payload)]
                    source = root/('sample.zip.001' if kind == 'zip-split' else 'sample.7z')
                subprocess.run(cmd, capture_output=True, check=True)
                before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
                self.assertEqual(preview.password_required(source, decoder), kind == '7z-hidden')
                with patch.object(ntfs_reclaim, 'reclaim_range', side_effect=AssertionError('mutation')):
                    result = preview.estimate(source, root/'out', password='secret' if kind == '7z-hidden' else None)
                self.assertEqual(result['ordinary_extra'], payload.stat().st_size)
                self.assertEqual(result['streaming_groups'], 0 if kind == 'zip-split' else 1)
                self.assertFalse((root/'out').exists())
                self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()})
                if kind == '7z':
                    with patch.object(preview, '_streaming_decoder', return_value=False):
                        fallback = preview.estimate(source, root/'out')
                    self.assertEqual(fallback['expected_extra'], fallback['cautious_extra'])


if __name__ == '__main__':
    unittest.main()
