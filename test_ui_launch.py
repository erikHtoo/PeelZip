"""Exercise the GUI launch boundary without creating a window or touching archives."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

import shrink_unzip_app as ui


class Events:
    def __init__(self, password=None):
        self.password = password
        self.items = []

    def put(self, event):
        self.items.append(event)
        if event[0] == 'password':
            event[1].put(self.password)


class LaunchTests(unittest.TestCase):
    def launch(self, encrypted, password=None, preview=False):
        source = Path('selected-archive.rar')
        args = [ui.PYTHON, '-u', str(ui.AGGRESSIVE), str(source), 'output']
        app = SimpleNamespace(events=Events(password), proc=None)
        process = Mock(stdout=iter([]))
        process.wait.return_value = 0
        with patch.object(ui.archive_password, 'required', return_value=encrypted) as detect, \
             patch.object(ui.subprocess, 'Popen', return_value=process) as spawn:
            ui.App._worker(app, args, not preview, source=source, script=ui.AGGRESSIVE)
        return app, args, detect, spawn

    def test_plain_archive_launches_without_prompt(self):
        app, args, detect, spawn = self.launch(False)
        self.assertEqual(detect.call_args.args[0], Path('selected-archive.rar'))
        self.assertEqual(spawn.call_args.args[0], args)
        self.assertEqual(app.events.items, [('done', 0)])

    def test_encrypted_archive_launches_after_reply(self):
        app, args, detect, spawn = self.launch(True, 'test-secret')
        self.assertEqual(detect.call_args.args[0], Path('selected-archive.rar'))
        self.assertEqual(spawn.call_args.args[0], args + ['--password', 'test-secret'])
        self.assertEqual([e[0] for e in app.events.items], ['password', 'done'])

    def test_cancel_does_not_start_extraction(self):
        app, _, _, spawn = self.launch(True)
        spawn.assert_not_called()
        self.assertEqual(app.events.items[-1], ('cancelled', None))

    def test_preview_does_not_prompt(self):
        app, args, detect, spawn = self.launch(True, preview=True)
        detect.assert_not_called()
        self.assertEqual(spawn.call_args.args[0], args)


if __name__ == '__main__':
    unittest.main()
