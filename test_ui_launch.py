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

    def test_aggressive_preview_prompts_only_for_hidden_metadata(self):
        source = Path('selected.7z')
        args = [ui.PYTHON, '-u', str(ui.PREVIEW), str(source), 'out']
        app = SimpleNamespace(events=Events('secret'), proc=None)
        process = Mock(stdout=iter([]))
        process.wait.return_value = 0
        with patch.object(ui.space_preview, 'password_required', return_value=True), \
             patch.object(ui.subprocess, 'Popen', return_value=process) as spawn:
            ui.App._worker(app, args, True, source=source, script=ui.PREVIEW)
        self.assertEqual(spawn.call_args.args[0], args + ['--password', 'secret'])

    def test_real_tk_preview_button_runs_read_only(self):
        import tempfile
        import time
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            source = Path(d)/'sample.zip'
            with zipfile.ZipFile(source, 'w') as z:
                z.writestr('file', b'preview test')
            before = source.read_bytes()
            try:
                app = ui.App()
            except ui.tk.TclError as exc:
                self.skipTest(str(exc))
            app.withdraw()
            try:
                app.zip_var.set(str(source))
                app.dest_var.set(str(Path(d)/'out'))
                self.assertEqual(str(app.preview_btn['state']), 'normal')
                with patch.object(ui.messagebox, 'askyesno', side_effect=AssertionError('Preview must not ask to destroy source')), \
                     patch.object(ui.messagebox, 'showinfo') as show, \
                     patch.object(ui.messagebox, 'showerror') as error:
                    app.preview_btn.invoke()
                    deadline = time.monotonic()+20
                    while getattr(app, 'running', False) and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.02)
                    self.assertFalse(app.running)
                    error.assert_not_called()
                    self.assertEqual(show.call_args.args[0], 'Space preview')
                    self.assertEqual(app.status.get(), 'Space estimate ready')
                self.assertEqual(source.read_bytes(), before)
                self.assertFalse((Path(d)/'out').exists())
            finally:
                if app.proc and app.proc.poll() is None:
                    app.proc.kill()
                    app.proc.wait()
                app.destroy()


if __name__ == '__main__':
    unittest.main()
