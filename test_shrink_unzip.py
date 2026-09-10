"""Regression tests. Run: py -m unittest -v test_shrink_unzip.py"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import shrink_unzip as app


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'fixture.zip'
        self.dest = self.root / 'result'
        self.payload = {'a.txt': b'apple' * 1000, 'sub/b.bin': bytes(range(256)) * 1000}
        self.make_zip(self.payload)

    def tearDown(self):
        self.tmp.cleanup()

    def make_zip(self, data):
        with zipfile.ZipFile(self.source, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, content in data.items():
                z.writestr(name, content)

    @property
    def state_dir(self):
        return Path(str(self.source) + '.shrink-state')

    def run_app(self, resume=False, execute=True):
        args = SimpleNamespace(zip=str(self.source), destination=str(self.dest),
                               execute=execute, resume=resume, accept_data_loss_risk=execute)
        with contextlib.redirect_stdout(io.StringIO()):
            app.run(args)

    def interrupt_at(self, count, after=True):
        original = app.sync_json
        def injected(path, state):
            if after:
                original(path, state)
            if state['completed'] == count and not state['done']:
                raise KeyboardInterrupt()
            if not after:
                original(path, state)
        with patch.object(app, 'sync_json', injected):
            with self.assertRaises(KeyboardInterrupt):
                self.run_app()

    def check_result(self):
        self.assertEqual(self.source.stat().st_size, 0)
        for name, data in self.payload.items():
            self.assertEqual((self.dest / name).read_bytes(), data)

    def test_preview_unchanged(self):
        before = self.source.read_bytes()
        self.run_app(execute=False)
        self.assertEqual(before, self.source.read_bytes())
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.state_dir.exists())

    def test_roundtrip(self):
        self.run_app()
        self.check_result()

    def test_symlink_entry_rejected(self):
        with zipfile.ZipFile(self.source, 'w') as z:
            info = zipfile.ZipInfo('link')
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, b'../outside')
        with self.assertRaises(ValueError):
            self.run_app(execute=False)

    def test_case_duplicate_rejected(self):
        self.make_zip({'A.txt': b'a', 'a.txt': b'b'})
        with self.assertRaises(ValueError):
            self.run_app(execute=False)

    def test_file_directory_conflict_rejected(self):
        self.make_zip({'a': b'a', 'a/b': b'b'})
        with self.assertRaises(ValueError):
            self.run_app(execute=False)

    def test_source_corruption_preflight_preserves_archive(self):
        with zipfile.ZipFile(self.source) as z:
            info = z.getinfo('a.txt')
        data = bytearray(self.source.read_bytes())
        data[info.header_offset + 30 + len(info.filename)] ^= 255
        self.source.write_bytes(data)
        with self.assertRaises(Exception):
            self.run_app()
        self.assertEqual(self.source.read_bytes(), data)
        self.assertFalse(self.state_dir.exists())

    def test_process_lock_excludes_second_process(self):
        import subprocess
        import sys
        with app.process_lock(self.source):
            result = subprocess.run([sys.executable, str(Path(app.__file__)), str(self.source),
                                     str(self.dest), '--execute', '--accept-data-loss-risk'],
                                    capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.dest.exists())

    def test_unknown_source_size_resume_refused(self):
        self.interrupt_at(1)
        with self.source.open('ab') as f:
            f.write(b'unexpected trailing bytes')
        before = self.source.read_bytes()
        with self.assertRaises(ValueError):
            self.run_app(resume=True)
        self.assertEqual(before, self.source.read_bytes())

    def test_all_entry_journal_boundaries(self):
        for count in (0, 1, 2):
            for after in (False, True):
                with self.subTest(count=count, after=after):
                    with tempfile.TemporaryDirectory(dir=self.root) as child:
                        old_source, old_dest = self.source, self.dest
                        self.source, self.dest = Path(child) / 'in.zip', Path(child) / 'out'
                        self.make_zip(self.payload)
                        self.interrupt_at(count, after)
                        self.run_app(resume=True)
                        self.check_result()
                        self.source, self.dest = old_source, old_dest

    def test_empty_zip_finalization_interruption(self):
        self.payload = {}
        self.make_zip({})
        original = app.sync_json
        def injected(path, state):
            if state['done']:
                raise KeyboardInterrupt()
            original(path, state)
        with patch.object(app, 'sync_json', injected):
            with self.assertRaises(KeyboardInterrupt):
                self.run_app()
        self.run_app(resume=True)
        self.check_result()

    def test_remaining_data_corruption_stops_without_further_shrink(self):
        self.interrupt_at(1, after=True)
        # The committed last entry is allowed to be reclaimed, but the corrupt first isn't.
        with zipfile.ZipFile(self.source) as z:
            info = z.getinfo('a.txt')
            cut = z.getinfo('sub/b.bin').header_offset
        with self.source.open('r+b') as f:
            f.seek(info.header_offset + 30 + len(info.filename))
            byte = f.read(1)
            f.seek(-1, 1)
            f.write(bytes([byte[0] ^ 255]))
        with self.assertRaises(Exception):
            self.run_app(resume=True)
        self.assertGreaterEqual(self.source.stat().st_size, cut)

    def test_changed_completed_output_refused(self):
        self.interrupt_at(1)
        before = self.source.read_bytes()
        (self.dest / 'sub/b.bin').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.run_app(resume=True)
        self.assertEqual(before, self.source.read_bytes())

    def test_changed_recovery_directory_refused(self):
        self.interrupt_at(1)
        before = self.source.read_bytes()
        tail = self.state_dir / 'directory.bin'
        data = bytearray(tail.read_bytes())
        # Alter a central-directory timestamp: remains a parsable ZIP directory.
        data[12] ^= 1
        tail.write_bytes(data)
        with self.assertRaises(ValueError):
            self.run_app(resume=True)
        self.assertEqual(before, self.source.read_bytes())

    def test_invalid_completed_count_refused(self):
        self.interrupt_at(0)
        path = self.state_dir / 'state.json'
        state = json.loads(path.read_text())
        state['completed'] = -1
        path.write_text(json.dumps(state))
        before = self.source.read_bytes()
        with self.assertRaises(ValueError):
            self.run_app(resume=True)
        self.assertEqual(before, self.source.read_bytes())

    def test_unsafe_names(self):
        for name in ('../escape', '/absolute', 'a\\b', 'a:stream', 'NUL.txt',
                     'COM¹.txt', 'trail./file', 'bad\x00hidden'):
            with self.subTest(name=name):
                # ZipInfo normalizes backslashes and strips NUL when *writing* on
                # Windows. Patch both on-disk names to construct an actual hostile ZIP.
                encoded_name = name.replace('\\', '/').replace('\x00', 'X')
                self.make_zip({encoded_name: b'data'})
                if encoded_name != name:
                    self.source.write_bytes(self.source.read_bytes().replace(
                        encoded_name.encode(), name.encode()))
                before = self.source.read_bytes()
                with self.assertRaises(ValueError):
                    self.run_app(execute=False)
                self.assertEqual(before, self.source.read_bytes())

    def test_implicit_directory_case_collision(self):
        self.make_zip({'Docs/a.txt': b'a', 'docs/b.txt': b'b'})
        with self.assertRaises(ValueError):
            self.run_app(execute=False)

    def test_directory_with_payload_rejected(self):
        self.make_zip({'folder/': b'not empty'})
        with self.assertRaises(ValueError):
            self.run_app(execute=False)

    def test_hardlinked_source_refused(self):
        os.link(self.source, self.root / 'alias.zip')
        with self.assertRaises(ValueError):
            self.run_app(execute=False)

    def test_low_space_preflight(self):
        before = self.source.read_bytes()
        with patch.object(app.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaises(ValueError):
                self.run_app()
        self.assertEqual(before, self.source.read_bytes())
        self.assertFalse(self.state_dir.exists())

    def test_space_runs_out_then_resume(self):
        def free(path):
            return SimpleNamespace(free=0 if Path(path) == self.dest else 10**12)
        with patch.object(app.shutil, 'disk_usage', side_effect=free):
            with self.assertRaises(OSError):
                self.run_app()
        self.run_app(resume=True)
        self.check_result()

    def test_interrupted_partial_write_then_resume(self):
        original = app.shutil.copyfileobj
        def fail(inp, out, length):
            if str(out.name).endswith('extracting.part'):
                out.write(inp.read(100))
                raise OSError('Injected disk write failure')
            original(inp, out, length)
        with patch.object(app.shutil, 'copyfileobj', fail):
            with self.assertRaises(OSError):
                self.run_app()
        self.run_app(resume=True)
        self.check_result()

    def test_methods_unicode_zip64(self):
        self.payload = {}
        with zipfile.ZipFile(self.source, 'w') as z:
            z.comment = b'Z' * 65535
            for method in (0, 8, 12, 14):
                name = f'資料/รายงาน {method}.bin'
                data = bytes(range(256)) * 100
                self.payload[name] = data
                info = zipfile.ZipInfo(name)
                info.compress_type = method
                with z.open(info, 'w', force_zip64=True) as f:
                    f.write(data)
        self.run_app()
        self.check_result()

    def test_zip64_end_records_resume(self):
        # Force actual ZIP64 end records without generating 65,536 files or 4 GiB.
        with patch.object(zipfile, 'ZIP_FILECOUNT_LIMIT', 1):
            self.make_zip(self.payload)
        self.assertIn(b'PK\x06\x06', self.source.read_bytes())
        self.interrupt_at(1)
        self.run_app(resume=True)
        self.check_result()


    def make_zstd(self):
        self.payload = {'資料/first.bin': bytes(range(256)) * 16384,
                        'second.bin': b'recover this zstandard file' * 65536}
        with app.zipfile.ZipFile(self.source, 'w', app.zipfile.ZIP_ZSTANDARD) as z:
            for name, data in self.payload.items():
                z.writestr(name, data)

    @unittest.skipUnless(app.HAS_ZSTANDARD, 'Zstandard backend not installed')
    def test_zstd_mixed_methods(self):
        self.payload = {}
        with app.zipfile.ZipFile(self.source, 'w') as z:
            for method in (0, 8, 12, 14, 93):
                name = f'資料/method-{method}.bin'
                data = bytes(range(256)) * 1024
                self.payload[name] = data
                z.writestr(name, data, compress_type=method)
        self.run_app()
        self.check_result()

    @unittest.skipUnless(app.HAS_ZSTANDARD, 'Zstandard backend not installed')
    def test_zstd_resume_before_truncation(self):
        self.make_zstd()
        self.interrupt_at(1)
        self.run_app(resume=True)
        self.check_result()

    @unittest.skipUnless(app.HAS_ZSTANDARD, 'Zstandard backend not installed')
    def test_zstd_resume_after_truncation(self):
        self.make_zstd()
        size = self.source.stat().st_size
        self.interrupt_at(2, after=False)
        self.assertLess(self.source.stat().st_size, size)
        self.run_app(resume=True)
        self.check_result()

    @unittest.skipUnless(app.HAS_ZSTANDARD, 'Zstandard backend not installed')
    def test_zstd_corruption_preserves_source(self):
        self.make_zstd()
        with app.zipfile.ZipFile(self.source) as z:
            info = z.infolist()[0]
        data = bytearray(self.source.read_bytes())
        data[info.header_offset + 30 + len(info.filename.encode('utf-8'))] ^= 255
        self.source.write_bytes(data)
        with self.assertRaises(Exception):
            self.run_app()
        self.assertEqual(self.source.read_bytes(), data)
        self.assertFalse(self.state_dir.exists())

    @unittest.skipUnless(app.HAS_ZSTANDARD, 'Zstandard backend not installed')
    def test_zstd_missing_dependency_message(self):
        self.make_zstd()
        before = self.source.read_bytes()
        with patch.object(app, 'HAS_ZSTANDARD', False):
            with self.assertRaisesRegex(ValueError, 'requirements.txt'):
                self.run_app(execute=False)
        self.assertEqual(self.source.read_bytes(), before)

    @unittest.skipUnless(app.HAS_ZSTANDARD, 'Zstandard backend not installed')
    def test_zstd_legacy_method_20_rejected_unchanged(self):
        import struct
        self.make_zstd()
        with app.zipfile.ZipFile(self.source) as z:
            local_offsets = [i.header_offset for i in z.infolist()]
            central = z.start_dir
        data = bytearray(self.source.read_bytes())
        for offset in local_offsets:
            struct.pack_into('<H', data, offset + 8, 20)
        while data[central:central+4] == b'PK\x01\x02':
            struct.pack_into('<H', data, central + 10, 20)
            name, extra, comment = struct.unpack_from('<HHH', data, central + 28)
            central += 46 + name + extra + comment
        self.source.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'compression method: 20'):
            self.run_app()
        self.assertEqual(self.source.read_bytes(), data)

    @unittest.skipUnless(app.HAS_ZSTANDARD, 'Zstandard backend not installed')
    def test_zstd_streaming_zip64(self):
        class ForwardOnly:
            def __init__(self, out):
                self.out = out
            def write(self, data):
                return self.out.write(data)
            def tell(self):
                return self.out.tell()
            def flush(self):
                self.out.flush()
            def seek(self, *args):
                raise io.UnsupportedOperation('Forward only')
        with self.source.open('wb') as raw, app.zipfile.ZipFile(ForwardOnly(raw), 'w') as z:
            z.comment = b'x' * 65535
            for name, data in self.payload.items():
                info = app.zipfile.ZipInfo(name)
                info.compress_type = app.zipfile.ZIP_ZSTANDARD
                with z.open(info, 'w', force_zip64=True) as out:
                    out.write(data)
        self.interrupt_at(1)
        self.run_app(resume=True)
        self.check_result()


if __name__ == '__main__':
    unittest.main()
