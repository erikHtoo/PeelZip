"""Shrink Unzip desktop UI. Python 3.10+, Tkinter standard library."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent
NORMAL = ROOT / 'shrink_unzip.py'
AGGRESSIVE = ROOT / 'aggressive_zstd_zip.py'
AGGRESSIVE_RAR = ROOT / 'aggressive_rar.py'
RAR = ROOT / 'rar_extract.py'
PYTHON = sys.executable


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Shrink Unzip')
        self.geometry('760x560')
        self.minsize(680, 480)
        self.events = queue.Queue()
        self.proc = None
        self._build()
        self.after(100, self._poll)

    def _build(self):
        outer = ttk.Frame(self, padding=18); outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Shrink Unzip', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='Extract large archives while reclaiming source storage.', foreground='#555').pack(anchor='w', pady=(0, 16))
        form = ttk.LabelFrame(outer, text='Archive and destination', padding=12); form.pack(fill='x')
        self.zip_var = tk.StringVar(); self.dest_var = tk.StringVar()
        self._row(form, 0, 'Archive', self.zip_var, self._browse_zip)
        self._row(form, 1, 'Destination', self.dest_var, self._browse_dest)
        mode = ttk.LabelFrame(outer, text='Mode', padding=12); mode.pack(fill='x', pady=(14, 0))
        self.mode = tk.StringVar(value='normal')
        ttk.Radiobutton(mode, text='Conservative (recommended)', variable=self.mode, value='normal', command=self._mode_changed).grid(row=0,column=0,sticky='w')
        ttk.Label(mode, text='Resumable per-file extraction; keeps normal ZIP behavior until completion.', foreground='#555').grid(row=1,column=0,sticky='w',padx=25)
        ttk.Radiobutton(mode, text='Aggressive experimental', variable=self.mode, value='aggressive', command=self._mode_changed).grid(row=2,column=0,sticky='w',pady=(10,0))
        ttk.Label(mode, text='Streams stored/Zstandard entries and reclaims source ranges; interruption can corrupt the archive.', foreground='#9b4d00', wraplength=650).grid(row=3,column=0,sticky='w',padx=25)
        controls = ttk.Frame(outer); controls.pack(fill='x', pady=(14,0))
        self.preview_btn = ttk.Button(controls, text='Preview space', command=lambda: self._start(False)); self.preview_btn.pack(side='left')
        self.run_btn = ttk.Button(controls, text='Start extraction', command=lambda: self._start(True)); self.run_btn.pack(side='left', padx=8)
        self.stop_btn = ttk.Button(controls, text='Stop', command=self._stop, state='disabled'); self.stop_btn.pack(side='left')
        self.progress = ttk.Progressbar(outer, mode='determinate', maximum=100); self.progress.pack(fill='x', pady=(20,4))
        self.status = tk.StringVar(value='Choose an archive to begin.')
        ttk.Label(outer, textvariable=self.status).pack(anchor='w')
        self.current = tk.StringVar(value='')
        ttk.Label(outer, textvariable=self.current, foreground='#555', wraplength=720).pack(anchor='w', pady=(2,8))
        log_frame = ttk.LabelFrame(outer, text='Output', padding=6); log_frame.pack(fill='both', expand=True)
        self.log = tk.Text(log_frame, height=12, state='disabled', wrap='none', font=('Consolas', 9)); self.log.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(log_frame, orient='vertical', command=self.log.yview); scroll.pack(side='right', fill='y'); self.log.configure(yscrollcommand=scroll.set)

    def _row(self, parent, row, label, variable, browse):
        ttk.Label(parent, text=label, width=12).grid(row=row,column=0,sticky='w',pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row,column=1,sticky='ew',padx=8,pady=5)
        ttk.Button(parent, text='Browse…', command=browse).grid(row=row,column=2,pady=5)
        parent.columnconfigure(1, weight=1)

    def _browse_zip(self):
        path = filedialog.askopenfilename(title='Choose archive', filetypes=[('ZIP/RAR archives','*.zip *.rar'),('ZIP archives','*.zip'),('RAR archives','*.rar'),('All files','*.*')])
        if path: self.zip_var.set(path); self._suggest_dest()

    def _browse_dest(self):
        path = filedialog.askdirectory(title='Choose a new destination folder')
        if path: self.dest_var.set(path)

    def _suggest_dest(self):
        p = Path(self.zip_var.get()); self.dest_var.set(str(p.with_name(p.stem + '-extracted')))

    def _mode_changed(self):
        if self.mode.get() == 'aggressive':
            self.status.set('Aggressive mode is destructive and experimental. Use a disposable archive.')

    def _append(self, text):
        self.log.configure(state='normal'); self.log.insert('end', text + '\n'); self.log.see('end'); self.log.configure(state='disabled')

    def _start(self, execute):
        if self.proc: return
        if not execute and self.mode.get() == 'aggressive':
            messagebox.showinfo('Preview unavailable', 'Aggressive mode has no safe preview because it must inspect and stream the source while reclaiming ranges. Use Conservative preview first.')
            return
        source, dest = self.zip_var.get().strip(), self.dest_var.get().strip()
        if not source or not dest: messagebox.showerror('Missing path', 'Choose both an archive and destination folder.'); return
        if execute and self.mode.get() == 'aggressive':
            ok = messagebox.askyesno('Aggressive mode warning', 'This mode permanently reclaims source archive ranges. An interruption may corrupt the archive. Continue?')
            if not ok: return
        script = AGGRESSIVE if self.mode.get() == 'aggressive' else NORMAL
        if Path(source).suffix.lower() == '.rar':
            script = AGGRESSIVE_RAR if self.mode.get() == 'aggressive' else RAR
        args = [PYTHON, '-u', str(script), source, dest]
        if execute:
            if self.mode.get() == 'normal':
                args += ['--execute', '--accept-data-loss-risk']
        self.progress.configure(value=0); self.current.set(''); self._append('$ ' + ' '.join('"'+x+'"' if ' ' in x else x for x in args))
        self._set_running(True)
        threading.Thread(target=self._worker, args=(args,), daemon=True).start()

    def _worker(self, args):
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
            for line in self.proc.stdout:
                self.events.put(('line', line.rstrip()))
            self.events.put(('done', self.proc.wait()))
        except Exception as e: self.events.put(('error', str(e)))

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            if messagebox.askyesno('Stop extraction?', 'Stopping may leave a partial output. Resume only according to the mode documentation.'):
                self.proc.terminate(); self.status.set('Stopping…')

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'line':
                    self._append(value)
                    m = re.search(r'\[(\d+)/(\d+)\]\s*(.*)', value)
                    if m:
                        self.progress.configure(value=100*int(m.group(1))/int(m.group(2))); self.current.set(m.group(3))
                    elif value.startswith('ZIP:') or value.startswith('Estimated'):
                        self.status.set(value)
                    elif value.startswith('Complete'):
                        self.progress.configure(value=100); self.status.set(value)
                elif kind == 'done':
                    code = value; self.proc = None; self._set_running(False)
                    self.status.set('Finished successfully.' if code == 0 else f'Stopped with exit code {code}.')
                    if code == 0: messagebox.showinfo('Shrink Unzip', 'Operation completed.')
                elif kind == 'error':
                    self.proc = None; self._set_running(False); self.status.set('Could not start operation.'); self._append(value); messagebox.showerror('Error', value)
        except queue.Empty: pass
        self.after(100, self._poll)

    def _set_running(self, running):
        state = 'disabled' if running else 'normal'
        self.preview_btn.configure(state='disabled' if running else 'normal'); self.run_btn.configure(state='disabled' if running else 'normal'); self.stop_btn.configure(state='normal' if running else 'disabled')


if __name__ == '__main__': App().mainloop()
