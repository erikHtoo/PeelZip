import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ntfs_reclaim as app


class ReclaimPrimitiveTests(unittest.TestCase):
    def test_invalid_ranges_rejected_before_platform_call(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x.bin'
            p.write_bytes(b'x' * 100)
            for offset, length in ((-1, 1), (0, 0), (99, 2), (101, 1)):
                with self.subTest(offset=offset, length=length):
                    with self.assertRaises(ValueError):
                        app.reclaim_range(p, offset, length)

    @unittest.skipUnless(os.name == 'nt', 'Windows NTFS test')
    def test_physical_allocation_drops_and_logical_size_remains(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x.bin'
            p.write_bytes(os.urandom(64 * 1024 * 1024))
            logical = p.stat().st_size
            before = app.allocated_bytes(p)
            app.reclaim_range(p, 16 * 1024 * 1024, 32 * 1024 * 1024)
            self.assertEqual(p.stat().st_size, logical)
            self.assertLess(app.allocated_bytes(p), before)
            with p.open('rb') as f:
                f.seek(16 * 1024 * 1024)
                self.assertEqual(f.read(1024), b'\0' * 1024)

    def test_non_windows_is_explicit(self):
        with patch.object(app.sys, 'platform', 'linux'):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'x.bin'
                p.write_bytes(b'x')
                with self.assertRaisesRegex(OSError, 'Windows'):
                    app.reclaim_range(p, 0, 1)


if __name__ == '__main__':
    unittest.main()
