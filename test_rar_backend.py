import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).parent))
import rar_backend as app

class Tests(unittest.TestCase):
    def test_missing_decoder_is_clear(self):
        with patch.object(app, 'find_decoder', return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, '7-Zip or WinRAR'):
                app.inspect('missing.rar')
    def test_inspect_parses_listing(self):
        output='''Type = Rar5\nSolid = -\nMultivolume = -\nEncrypted = -\n\nPath = game/file.bin\nFolder = -\nSize = 123\n\nPath = game\nFolder = +\n'''
        fake=Path('fake-7z.exe')
        completed=type('R',(),{'returncode':0,'stdout':output,'stderr':''})()
        with patch.object(app.subprocess, 'run', return_value=completed):
            result=app.inspect('x.rar',fake)
        self.assertEqual(result['entries'][0]['Path'],'game/file.bin')
        self.assertFalse(result['solid'])
    def test_aggressive_is_explicitly_unavailable(self):
        self.assertEqual(app.aggressive_supported({})[0],False)

if __name__=='__main__':unittest.main()
