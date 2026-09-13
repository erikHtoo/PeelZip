import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import aggressive_rar
import ntfs_reclaim
import rar_backend
import rar_parts


class MultipartRAR(unittest.TestCase):
    def test_preflight_rejects_hidden_headers_and_missing_volume(self):
        encoder = Path('C:/Program Files/WinRAR/Rar.exe')
        if not encoder.exists():
            self.skipTest('WinRAR encoder required')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'data.bin').write_bytes(os.urandom(2500000))
            for hidden in (False, True):
                name = 'hidden' if hidden else 'missing'
                cmd = [str(encoder), 'a', '-ep1', '-v1m']
                if hidden:
                    cmd.append('-hptest123')
                subprocess.run(cmd + [str(root/f'{name}.rar'), str(root/'data.bin')], capture_output=True, check=True)
                source = root/f'{name}.part1.rar'
                volumes = aggressive_rar._rar_volumes(source)
                if not hidden:
                    volumes[1].rename(root/'removed-volume')
                    volumes.pop(1)
                before = [v.read_bytes() for v in volumes]
                with patch.object(ntfs_reclaim, 'reclaim_range') as reclaim:
                    with self.assertRaises((ValueError, RuntimeError)):
                        aggressive_rar.extract_aggressive(source, root/f'out-{name}', password='test123')
                    reclaim.assert_not_called()
                self.assertEqual(before, [v.read_bytes() for v in volumes])

    def test_real_incremental_volumes(self):
        encoder = Path('C:/Program Files/WinRAR/Rar.exe')
        if not encoder.exists():
            self.skipTest('WinRAR encoder required')
        for solid, password in [(False, None), (True, None), (False, 'test123')]:
            with self.subTest(solid=solid, password=bool(password)), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                originals = {f'{n}.bin': os.urandom(1300000) for n in range(3)}
                for name, data in originals.items():
                    (root/name).write_bytes(data)
                cmd = [str(encoder), 'a', '-ep1', '-m1', '-s' if solid else '-s-', '-v1m']
                if password:
                    cmd.append('-p' + password)
                subprocess.run(cmd + [str(root/'sample.rar')] + [str(root/n) for n in originals], capture_output=True, check=True)
                source = root/'sample.part1.rar'
                volumes = aggressive_rar._rar_volumes(source)
                self.assertGreater(len(volumes), 2)
                meta = rar_backend.inspect(source, password=password)
                mapping = rar_parts.payloads(volumes, meta['entries'])
                self.assertTrue(any(len(p) > 1 for p in mapping))
                before = [v.read_bytes() for v in volumes]
                bad = [dict(e) for e in meta['entries']]
                bad[0]['PackSize'] += 1
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    rar_parts.payloads(volumes, bad)
                with volumes[-1].open('r+b') as stream:
                    stream.seek(8)
                    crc = stream.read(1)
                    stream.seek(8)
                    stream.write(bytes([crc[0] ^ 1]))
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    rar_parts.payloads(volumes, meta['entries'])
                volumes[-1].write_bytes(before[-1])
                out = root/'out'
                calls = []
                original = ntfs_reclaim.reclaim_range
                def reclaim(path, offset, length):
                    calls.append(sum((out/n).exists() for n in originals))
                    original(path, offset, length)
                with patch.object(ntfs_reclaim, 'reclaim_range', side_effect=reclaim):
                    result = aggressive_rar.extract_aggressive(source, out, password=password, verify=True)
                self.assertTrue(calls)
                self.assertEqual(calls[0], 3 if solid else 1)
                for name, data in originals.items():
                    self.assertEqual((out/name).read_bytes(), data)
                self.assertGreater(result['allocated_bytes_freed'], 3000000)
                self.assertEqual(result['reclaimed_bytes'], sum(e['PackSize'] for e in meta['entries']))
                for vi, volume in enumerate(volumes):
                    expected = bytearray(before[vi])
                    for parts in mapping:
                        for index, offset, length in parts:
                            if index == vi:
                                expected[offset:offset+length] = bytes(length)
                    self.assertEqual(volume.read_bytes(), expected, 'Only payload bytes may change')
                resumed = aggressive_rar.extract_aggressive(source, out, password=password, verify=True, resume=True)
                self.assertEqual(resumed['reclaimed_bytes'], 0)
                print(f'RAR multipart solid={solid} encrypted={bool(password)}: freed {result["allocated_bytes_freed"]} bytes; first reclaim after {calls[0]} of 3 files')


if __name__ == '__main__':
    unittest.main()
