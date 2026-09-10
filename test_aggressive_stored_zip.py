import os, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent))
import aggressive_stored_zip as app

class Tests(unittest.TestCase):
    def make(self, root):
        src = root/'x.zip'; data = {'a.bin': os.urandom(3*1024*1024), 'b.bin': os.urandom(2*1024*1024)}
        with zipfile.ZipFile(src, 'w', compression=zipfile.ZIP_STORED) as z:
            for n, d in data.items(): z.writestr(n, d)
        return src, data
    def test_roundtrip_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); src,data=self.make(root); dst=root/'out'
            original=app.reclaim_range
            calls=[]
            def fail(path, off, length):
                calls.append(1)
                if len(calls)==2: raise KeyboardInterrupt()
                return original(path, off, length)
            with patch.object(app, 'reclaim_range', fail):
                with self.assertRaises(KeyboardInterrupt): app.run(src,dst,accept=True,chunk_size=1024*1024)
            app.run(src,dst,resume=True,accept=True,chunk_size=1024*1024)
            for n,dta in data.items(): self.assertEqual((dst/n).read_bytes(),dta)
            self.assertGreater(src.stat().st_size, 0)
    def test_compressed_rejected_without_change(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); src=root/'x.zip'
            with zipfile.ZipFile(src,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('x',b'x')
            before=src.read_bytes()
            with self.assertRaises(ValueError): app.run(src,root/'out',accept=True)
            self.assertEqual(before,src.read_bytes())
if __name__=='__main__': unittest.main()
