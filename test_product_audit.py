import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import aggressive_zip
import archive_kind
import archive_checks
import ntfs_reclaim
import zip_stream
import subprocess
import aggressive_rar
import aggressive_7z


class ProductAudit(unittest.TestCase):
    def test_encrypted_split_and_7z_real_decoder(self):
        decoder = Path(__file__).parent / 'tools' / '7zz.exe'
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            data = os.urandom(2 * 1024 * 1024)
            (p / 'a.bin').write_bytes(data)
            for fmt in ('zip', '7z'):
                src = p / f'archive.{fmt}'
                subprocess.run([str(decoder), 'a', '-v1m', '-ptest123', str(src), str(p/'a.bin')], capture_output=True, check=True)
                source = Path(str(src) + '.001')
                out = p / f'out-{fmt}'
                fn = aggressive_zip.run if fmt == 'zip' else aggressive_7z.extract_aggressive
                fn(source, out, password='test123', verify=True)
                self.assertEqual((out/'a.bin').read_bytes(), data)

    def test_real_rar_solid_and_non_solid(self):
        encoder = Path('C:/Program Files/WinRAR/Rar.exe')
        if not encoder.exists():
            self.skipTest('RAR encoder not installed')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            data = os.urandom(2 * 1024 * 1024)
            (p/'a.bin').write_bytes(data)
            (p/'b.bin').write_bytes(data)
            for solid in (False, True):
                src = p/f'{solid}.rar'
                subprocess.run([str(encoder), 'a', '-ep1', '-s' if solid else '-s-', str(src), str(p/'a.bin'), str(p/'b.bin')], capture_output=True, check=True)
                out = p/f'out-{solid}'
                result = aggressive_rar.extract_aggressive(src, out, verify=True)
                self.assertEqual((out/'a.bin').read_bytes(), data)
                self.assertEqual((out/'b.bin').read_bytes(), data)
                self.assertGreater(result['allocated_bytes_freed'], 1024 * 1024)

    def test_content_wins_and_unknown_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'wrong.rar'
            with zipfile.ZipFile(p, 'w') as z:
                z.writestr('file', b'test')
            self.assertEqual(archive_kind.detect(p), 'zip')
            p.write_bytes(b'not an archive')
            self.assertIsNone(archive_kind.detect(p))

    def test_paths_and_conflicts(self):
        for names in [['../escape'], ['C:/escape'], ['NUL.txt'], ['a', 'a/b'], ['A', 'a']]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                archive_checks.targets('test-output', names)

    def test_real_streaming_all_methods(self):
        methods = [0, 8, 12, 14]
        zf = aggressive_zip.zipfile
        if hasattr(zf, 'ZIP_ZSTANDARD'):
            methods.append(zf.ZIP_ZSTANDARD)
        for method in methods:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                src = root / 'mislabeled.rar'
                out = root / 'out'
                data = os.urandom(3 * 1024 * 1024)
                with zf.ZipFile(src, 'w', compression=method) as z:
                    z.writestr('a.bin', data)
                    z.writestr('tail.txt', b'tail intact')
                original_reclaim = ntfs_reclaim.reclaim_range
                mid_file = []
                def reclaim(path, offset, length):
                    target = out / 'a.bin'
                    if target.exists() and 0 < target.stat().st_size < len(data):
                        mid_file.append(True)
                    original_reclaim(path, offset, length)
                with patch.object(ntfs_reclaim, 'reclaim_range', side_effect=reclaim):
                    result = aggressive_zip.run(src, out)
                self.assertTrue(mid_file, 'must reclaim before the large output finishes')
                self.assertEqual((out / 'a.bin').read_bytes(), data)
                self.assertEqual((out / 'tail.txt').read_bytes(), b'tail intact')
                self.assertGreater(result['allocated_bytes_freed'], 1024 * 1024)
                resumed = aggressive_zip.run(src, out, resume=True)
                self.assertEqual(resumed['reclaimed_bytes'], 0)

    def test_reject_path_before_reclaim(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.zip'
            with zipfile.ZipFile(p, 'w') as z:
                z.writestr('../escape', 'bad')
            before = p.read_bytes()
            with self.assertRaises(ValueError):
                aggressive_zip.run(p, Path(d) / 'out')
            self.assertEqual(before, p.read_bytes())

    def test_interrupted_stream_is_not_claimed_resumable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.zip'
            out = Path(d) / 'out'
            with zipfile.ZipFile(p, 'w') as z:
                z.writestr('file', os.urandom(2 * 1024 * 1024))
            with patch.object(ntfs_reclaim, 'reclaim_range', side_effect=OSError('injected')):
                with self.assertRaises(OSError):
                    aggressive_zip.run(p, out)
            with self.assertRaisesRegex(RuntimeError, 'cannot resume'):
                aggressive_zip.run(p, out, resume=True)


if __name__ == '__main__':
    unittest.main()
