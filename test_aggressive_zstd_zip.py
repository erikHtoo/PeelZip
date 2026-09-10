import os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).parent))
import aggressive_zstd_zip as app

@unittest.skipUnless(app.zstd is not None and os.name=='nt','Windows with Zstandard backend')
class Tests(unittest.TestCase):
    def test_streams_and_reclaims(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); src=root/'x.zip'; data=os.urandom(12*1024*1024)
            with app.zipfile.ZipFile(src,'w') as z:z.writestr('big.bin',data,compress_type=93)
            before=src.stat().st_size
            app.run(src,root/'out',accept=True)
            self.assertEqual((root/'out/big.bin').read_bytes(),data)
            self.assertEqual(src.stat().st_size,before)
    def test_failure_is_reported_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); src=root/'x.zip'
            with app.zipfile.ZipFile(src,'w') as z:z.writestr('x',b'hello',compress_type=93)
            with patch.object(app,'reclaim_range',side_effect=OSError('disk test')):
                with self.assertRaises(OSError):app.run(src,root/'out',accept=True)
if __name__=='__main__':unittest.main()
