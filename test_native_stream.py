import os
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import aggressive_7z
import aggressive_rar
import native_stream
import ntfs_reclaim


class NativeStreaming(unittest.TestCase):
    def test_rejects_duplicate_and_outside_ranges(self):
        tracker = native_stream.Reclaimer([(Path('source'), 100, 100)])
        with patch.object(ntfs_reclaim, 'reclaim_range') as reclaim:
            tracker.consume([(Path('source'), 125, 25)])
            for start, length in [(125, 25), (99, 1), (190, 11), (100, 0)]:
                with self.assertRaises(RuntimeError):
                    tracker.consume([(Path('source'), start, length)])
            self.assertEqual(reclaim.call_count, 1)
            tracker.finish()
            self.assertEqual(tracker.reclaimed, 100)

    def test_large_file_and_solid_streaming(self):
        decoder = Path(__file__).parent/'tools'/'7zz.exe'
        encoder = Path('C:/Program Files/WinRAR/Rar.exe')
        cases = [('7z-lzma2', None), ('7z-bcj2', None), ('7z-split', 'test123'), ('7z-hidden', 'test123')]
        if encoder.exists():
            cases += [('rar-single', None), ('rar-solid', None), ('rar-split', 'test123')]
        for kind, password in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                data = os.urandom(12 * 1024 * 1024)
                originals = {'first.bin': data, 'second.bin': data[::-1]}
                for name, value in originals.items():
                    (root/name).write_bytes(value)
                is_rar = kind.startswith('rar')
                source = root/('sample.rar' if is_rar else 'sample.7z')
                if is_rar:
                    cmd = [str(encoder), 'a', '-ep1', '-m1', '-s-' if kind == 'rar-single' else '-s']
                else:
                    cmd = [str(decoder), 'a', '-mx=1', '-ms=on']
                    if kind == '7z-bcj2':
                        cmd += ['-m0=BCJ2', '-m1=LZMA2', '-m2=LZMA2', '-m3=LZMA2', '-mb0:1', '-mb0s1:2', '-mb0s2:3']
                if 'split' in kind:
                    cmd.append('-v5m')
                if password:
                    cmd.append('-p' + password)
                if kind == '7z-hidden':
                    cmd.append('-mhe=on')
                subprocess.run(cmd + [str(source)] + [str(root/n) for n in originals], capture_output=True, check=True)
                if 'split' in kind:
                    source = root/('sample.part1.rar' if is_rar else 'sample.7z.001')
                volumes = aggressive_rar._rar_volumes(source) if is_rar else aggressive_7z._volumes(source)
                initial = sum(ntfs_reclaim.allocated_bytes(v) for v in volumes)
                out = root/'out'
                fn = aggressive_rar.extract_aggressive if is_rar else aggressive_7z.extract_aggressive
                original_reclaim = ntfs_reclaim.reclaim_range
                observations = []
                peaks = [0]
                def reclaim(path, offset, length):
                    output = sum(ntfs_reclaim.allocated_bytes(out/n) for n in originals if (out/n).exists())
                    source_bytes = sum(ntfs_reclaim.allocated_bytes(v) for v in volumes)
                    peaks.append(source_bytes + output - initial)
                    observations.append(sum((out/n).stat().st_size for n in originals if (out/n).exists()))
                    original_reclaim(path, offset, length)
                with patch.object(ntfs_reclaim, 'reclaim_range', side_effect=reclaim):
                    result = fn(source, out, password=password, verify=True)
                self.assertGreater(result['streamed_bytes'], 20 * 1024 * 1024)
                self.assertTrue(any(n < len(data) for n in observations), 'Must reclaim before the first large file finishes')
                for name, value in originals.items():
                    self.assertEqual((out/name).read_bytes(), value)
                baseline = sum(ntfs_reclaim.allocated_bytes(out/n) for n in originals)
                peaks.append(baseline + sum(ntfs_reclaim.allocated_bytes(v) for v in volumes) - initial)
                self.assertLess(max(peaks), baseline * 0.6)
                resumed = fn(source, out, password=password, verify=True, resume=True)
                self.assertEqual(resumed['reclaimed_bytes'], 0)
                if kind == 'rar-solid':
                    journal = aggressive_rar._state_path(out)
                    legacy = json.loads(journal.read_text(encoding='utf-8'))
                    legacy['entries'].pop('1')
                    journal.write_text(json.dumps(legacy), encoding='utf-8')
                    with self.assertRaisesRegex(RuntimeError, 'partially completed solid'):
                        fn(source, out, password=password, resume=True)
                print(f'{kind}: sampled peak extra {max(peaks)} bytes vs {baseline} ordinary output allocation; streamed {result["streamed_bytes"]} bytes')

    def test_failed_reclaim_terminates_decoder_and_refuses_resume(self):
        decoder = Path(__file__).parent/'tools'/'7zz.exe'
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'data.bin').write_bytes(os.urandom(3 * 1024 * 1024))
            source = root/'sample.7z'
            subprocess.run([str(decoder), 'a', '-mx=1', str(source), str(root/'data.bin')], capture_output=True, check=True)
            with patch.object(ntfs_reclaim, 'reclaim_range', side_effect=OSError('injected reclaim failure')):
                with self.assertRaisesRegex(OSError, 'injected'):
                    aggressive_7z.extract_aggressive(source, root/'out')
            with self.assertRaisesRegex(RuntimeError, 'Cannot resume'):
                aggressive_7z.extract_aggressive(source, root/'out', resume=True)


if __name__ == '__main__':
    unittest.main()
