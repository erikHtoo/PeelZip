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
import archive_kind
from ui_help import HELP, HelpButton
from tkinter import filedialog, messagebox, simpledialog, ttk
import archive_password
import space_preview

ROOT = Path(__file__).resolve().parent
NORMAL = ROOT / 'shrink_unzip.py'
AGGRESSIVE = ROOT / 'aggressive_zip.py'
AGGRESSIVE_RAR = ROOT / 'aggressive_rar.py'
AGGRESSIVE_7Z = ROOT / 'aggressive_7z.py'
AGGRESSIVE_GENERIC = ROOT / 'aggressive_generic.py'
GENERIC = ROOT / 'generic_extract.py'
RAR = ROOT / 'rar_extract.py'
PYTHON = sys.executable
PREVIEW = ROOT / 'space_preview.py'


def _is_zip_path(path):
    value = str(path).lower()
    return value.endswith(('.zip', '.zip.001')) or re.search(r'\.z\d\d$', value) is not None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('PeelZip')
        self.geometry('760x560')
        self.minsize(680, 480)
        self.events = queue.Queue()
        self.proc = None
        self._build()
        self.after(100, self._poll)

    def _build(self):
        self.configure(bg='#111512')
        self.geometry('760x560')
        self.minsize(700, 520)
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background='#111512', foreground='#edf1e9', font=('Segoe UI', 10))
        style.configure('TFrame', background='#111512')
        style.configure('Card.TFrame', background='#1b211c')
        style.configure('TLabel', background='#111512', foreground='#edf1e9')
        style.configure('Muted.TLabel', foreground='#9ca89d')
        style.configure('Card.TLabel', background='#1b211c')
        style.configure('Hint.TLabel', background='#1b211c', foreground='#9ca89d', font=('Segoe UI', 9))
        style.configure('Title.TLabel', font=('Segoe UI Semibold', 26))
        style.configure('TButton', padding=(16,10), background='#29322b', foreground='#edf1e9', borderwidth=0, focusthickness=1, focuscolor='#d6f578')
        style.map('TButton', background=[('active','#38443a'),('disabled','#202722')], foreground=[('disabled','#748177')])
        style.configure('Primary.TButton', background='#d6f578', foreground='#172014', font=('Segoe UI Semibold',11))
        style.map('Primary.TButton', background=[('active','#e3ffa0'),('disabled','#303a29')], foreground=[('disabled','#849078')])
        style.configure('TEntry', fieldbackground='#111512', foreground='#edf1e9', insertcolor='#d6f578', bordercolor='#38433a', lightcolor='#38433a', darkcolor='#38433a', padding=10)
        style.configure('TRadiobutton', background='#1b211c', foreground='#edf1e9', indicatorcolor='#111512', padding=4)
        style.map('TRadiobutton', background=[('active','#1b211c')], indicatorcolor=[('selected','#d6f578')])
        style.configure('TCheckbutton', background='#111512', foreground='#c0cbbf', padding=3)
        style.map('TCheckbutton', background=[('active','#111512')], indicatorcolor=[('selected','#d6f578')])
        style.configure('Horizontal.TProgressbar', troughcolor='#29322b', background='#d6f578', bordercolor='#29322b', lightcolor='#29322b', darkcolor='#29322b', borderwidth=0, thickness=6)
        viewport=tk.Canvas(self,bg='#111512',highlightthickness=0)
        scrollbar=ttk.Scrollbar(self,orient='vertical',command=viewport.yview)
        viewport.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y');viewport.pack(side='left',fill='both',expand=True)
        outer = ttk.Frame(viewport, padding=(24,18))
        content=viewport.create_window((0,0),window=outer,anchor='nw')
        outer.bind('<Configure>',lambda e:viewport.configure(scrollregion=viewport.bbox('all')))
        viewport.bind('<Configure>',lambda e:viewport.itemconfigure(content,width=e.width))
        self.bind('<MouseWheel>',lambda e:viewport.yview_scroll(-int(e.delta/120),'units'))
        header = ttk.Frame(outer); header.pack(fill='x',pady=(0,16))
        try:
            raw = tk.PhotoImage(file=str(ROOT/'assets'/'peelzip-mark.png'))
            self.brand_image = raw.subsample(max(1,raw.width()//54))
            self.iconphoto(True, self.brand_image)
            ttk.Label(header, image=self.brand_image).pack(side='left', padx=(0,10))
        except tk.TclError:
            pass
        ttk.Label(header, text='peelzip', font=('Segoe UI Semibold',19)).pack(side='left')
        self.zip_var = tk.StringVar(); self.dest_var = tk.StringVar()
        card = ttk.Frame(outer, style='Card.TFrame', padding=18); card.pack(fill='x')
        self._row(card, 0, 'Source', self.zip_var, self._browse_zip)
        self._row(card, 1, 'Destination', self.dest_var, self._browse_dest)
        self.file_hint = tk.StringVar(value='')
        ttk.Label(card, textvariable=self.file_hint, style='Hint.TLabel').grid(row=2,column=0,columnspan=3,sticky='w',pady=(8,0))
        self.zip_var.trace_add('write', self._file_summary)
        modes = ttk.Frame(outer); modes.pack(fill='x',pady=(16,12)); modes.columnconfigure((0,1),weight=1,uniform='mode')
        self.mode = tk.StringVar(value='aggressive'); self.verify_var = tk.BooleanVar(value=True); self.resume_var = tk.BooleanVar(value=False)
        for column, value, title in [(0,'aggressive','Aggressive'),(1,'normal','Conservative')]:
            frame=ttk.Frame(modes,style='Card.TFrame',padding=14);frame.grid(row=0,column=column,sticky='nsew',padx=(0,8) if column==0 else (8,0))
            ttk.Radiobutton(frame,text=title,value=value,variable=self.mode,command=self._mode_changed).pack(side='left')
            HelpButton(frame,HELP[value],background='#1b211c').pack(side='right')
        self.mode_hint=tk.StringVar(value='Source bytes are consumed. An interrupted run may need a new download.')
        self.options_btn=ttk.Button(outer,text='Options',command=self._toggle_options);self.options_btn.pack(anchor='w')
        self.options=ttk.Frame(outer,padding=(0,8))
        verify_row=ttk.Frame(self.options);verify_row.grid(row=1,column=0,columnspan=2,sticky='w',pady=(6,0))
        ttk.Checkbutton(verify_row,text='Verify output',variable=self.verify_var).pack(side='left')
        HelpButton(verify_row,HELP['verify']).pack(side='left',padx=8)
        resume_row=ttk.Frame(self.options);resume_row.grid(row=2,column=0,columnspan=2,sticky='w',pady=(6,0))
        ttk.Checkbutton(resume_row,text='Resume completed files',variable=self.resume_var).pack(side='left')
        HelpButton(resume_row,HELP['resume']).pack(side='left',padx=8)
        self.activity=ttk.Frame(outer,padding=(0,16));self.activity.pack(fill='x')
        line=ttk.Frame(self.activity);line.pack(fill='x')
        self.status=tk.StringVar(value='Ready');self.percent=tk.StringVar(value='0%')
        ttk.Label(line,textvariable=self.status,font=('Segoe UI Semibold',11)).pack(side='left')
        ttk.Label(line,textvariable=self.percent,style='Muted.TLabel').pack(side='right')
        self.progress=ttk.Progressbar(self.activity,maximum=100);self.progress.pack(fill='x',pady=(12,8))
        self.current=tk.StringVar(value='')
        ttk.Label(self.activity,textvariable=self.current,style='Muted.TLabel',wraplength=770,font=('Segoe UI',9)).pack(anchor='w')
        self.controls=ttk.Frame(outer);self.controls.pack(fill='x',pady=(0,8))
        preview_row=ttk.Frame(self.options);preview_row.grid(row=3,column=0,sticky='w',pady=(8,0))
        self.preview_btn=ttk.Button(preview_row,text='Preview space',command=lambda:self._start(False));self.preview_btn.pack(side='left')
        HelpButton(preview_row,HELP['preview']).pack(side='left',padx=8)
        self.stop_btn=ttk.Button(self.controls,text='Stop',command=self._stop,state='disabled');self.stop_btn.pack_forget()
        self.run_btn=ttk.Button(self.controls,text='Extract',style='Primary.TButton',command=lambda:self._start(True));self.run_btn.pack(side='right')
        log_row=ttk.Frame(outer);log_row.pack(anchor='w',pady=(4,0))
        self.log_btn=ttk.Button(log_row,text='Activity log',command=self._toggle_log);self.log_btn.pack(side='left')
        HelpButton(log_row,HELP['log']).pack(side='left',padx=8)
        self.log_frame=ttk.Frame(outer,padding=(0,8))
        self.log=tk.Text(self.log_frame,height=5,state='disabled',wrap='word',bg='#0c100d',fg='#a9b7aa',insertbackground='#d6f578',relief='flat',padx=12,pady=10,font=('Consolas',9))
        self.log.pack(fill='both',expand=True)
        self._mode_changed()

    def _row(self, parent, row, label, variable, browse):
        ttk.Label(parent,text=label,style='Hint.TLabel',width=12).grid(row=row,column=0,sticky='w',pady=6)
        ttk.Entry(parent,textvariable=variable).grid(row=row,column=1,sticky='ew',padx=(4,12),pady=6)
        ttk.Button(parent,text='Browse',command=browse).grid(row=row,column=2)
        parent.columnconfigure(1,weight=1)

    def _toggle_options(self):
        if self.options.winfo_manager():
            self.options.pack_forget();self.options_btn.configure(text='Options')
        else:
            self.options.pack(fill='x',before=self.activity);self.options_btn.configure(text='Hide options')


    def _toggle_log(self):
        if self.log_frame.winfo_manager():
            self.log_frame.pack_forget();self.log_btn.configure(text='Activity log')
        else:
            self.log_frame.pack(fill='both',expand=True);self.log_btn.configure(text='Hide log')


    def _file_summary(self, *args):
        import shutil
        path=Path(self.zip_var.get())
        try:
            kind=archive_kind.detect(path)
            self.file_hint.set(f'{(kind or "Unknown format").upper()}  \xb7  {path.stat().st_size/1024**3:.2f} GiB archive  \xb7  {shutil.disk_usage(path.parent).free/1024**3:.2f} GiB free')
        except (OSError,ValueError):
            self.file_hint.set('')

    def _browse_zip(self):
        path = filedialog.askopenfilename(title='Choose archive', filetypes=[('ZIP/RAR/7z archives','*.zip *.zip.001 *.z01 *.rar *.7z *.7z.001 *.tar *.gz *.iso *.cab *.wim'),('ZIP archives','*.zip *.zip.001 *.z01'),('RAR archives','*.rar'),('7z archives','*.7z'),('All files','*.*')])
        if path: self.zip_var.set(path); self._suggest_dest()

    def _browse_dest(self):
        path = filedialog.askdirectory(title='Choose a new destination folder')
        if path: self.dest_var.set(path)

    def _suggest_dest(self):
        p = Path(self.zip_var.get()); self.dest_var.set(str(p.with_name(p.stem + '-extracted')))

    def _mode_changed(self):
        aggressive=self.mode.get()=='aggressive'
        self.mode_hint.set('Source bytes are consumed. An interrupted run may need a new download.' if aggressive else 'ZIP source is consumed file by file. Other supported formats extract normally.')
        self.preview_btn.configure(state='disabled' if getattr(self, 'running', False) else 'normal')

    def _append(self, text):
        self.log.configure(state='normal'); self.log.insert('end', text + '\n'); self.log.see('end'); self.log.configure(state='disabled')

    def _start(self, execute):
        if self.proc or getattr(self, 'running', False): return
        source, dest = self.zip_var.get().strip(), self.dest_var.get().strip()
        if not source or not dest: messagebox.showerror('Missing path', 'Choose both an archive and destination folder.'); return
        try:
            kind = archive_kind.detect(source)
        except OSError as exc:
            messagebox.showerror('Cannot read archive', str(exc)); return
        if kind is None:
            messagebox.showerror('Unsupported archive', 'Could not identify this file as ZIP, RAR, or 7z.'); return
        if kind == '7z' and self.mode.get() != 'aggressive':
            messagebox.showinfo('7z mode', 'PeelZip currently supports 7z only in aggressive storage-saving mode. Use 7-Zip for ordinary extraction.')
            return
        if kind in {'tar', 'gz', 'iso', 'cab', 'wim'} and self.mode.get() == 'aggressive':
            messagebox.showinfo('No incremental reclamation', 'This format currently requires the full output space. Incremental aggressive extraction is not implemented.'); return
        if not execute and self.mode.get() != 'aggressive' and kind != 'zip':
            messagebox.showinfo('Preview unavailable', 'Space preview is currently available for conservative ZIP only.'); return
        if execute and self.mode.get() == 'aggressive':
            ok = messagebox.askyesno('Aggressive mode warning', 'This mode permanently reclaims source archive ranges. An interruption may corrupt the archive. Continue?')
            if not ok: return
        script = AGGRESSIVE if self.mode.get() == 'aggressive' else NORMAL
        if kind == 'rar':
            script = AGGRESSIVE_RAR if self.mode.get() == 'aggressive' else RAR
        if kind == '7z':
            script = AGGRESSIVE_7Z
        if kind in {'tar', 'gz', 'iso', 'cab', 'wim'}:
            script = AGGRESSIVE_GENERIC if self.mode.get() == 'aggressive' else GENERIC
        if kind == 'zip' and self.mode.get() == 'aggressive':
            script = AGGRESSIVE
        if not execute and self.mode.get() == 'aggressive':
            script = PREVIEW
        self.preview_result = None
        self.preview_error = None
        args = [PYTHON, '-u', str(script), source, dest]
        if execute:
            if self.mode.get() == 'normal' and kind == 'zip':
                args += ['--execute', '--accept-data-loss-risk']
            elif self.mode.get() == 'aggressive' and kind == 'rar' and self.verify_var.get():
                args += ['--verify']
            elif kind == '7z' and self.verify_var.get():
                args += ['--verify']
            if self.mode.get() == 'aggressive' and kind == 'rar' and self.resume_var.get():
                args += ['--resume']
            if self.mode.get() == 'aggressive' and kind == '7z' and self.resume_var.get():
                args += ['--resume']
            if self.mode.get() == 'aggressive' and kind == 'zip' and self.resume_var.get():
                args += ['--resume']
        if execute and kind == 'zip' and self.mode.get() == 'aggressive' and not self.verify_var.get():
            args.append('--no-verify')
        self.status.set('Extracting\u2026' if execute else 'Calculating space\u2026')
        self.percent.set('0%')
        self.active_file = (0, 1)
        display_args = ['-p********' if x.startswith('--password') else ('********' if i and args[i-1] == '--password' else x) for i, x in enumerate(args)]
        self.progress.configure(value=0); self.current.set(''); self._append('$ ' + ' '.join('"'+x+'"' if ' ' in x else x for x in display_args))
        self._set_running(True)
        threading.Thread(target=self._worker, args=(args, execute or script == PREVIEW),
                         kwargs={'source': source, 'script': script}, daemon=True).start()

    def _worker(self, args, check_password=True, *, source, script):
        try:
            detect_password = space_preview.password_required if Path(script).resolve() == PREVIEW.resolve() else archive_password.required
            if check_password and detect_password(Path(source), ROOT/'tools'/'7zz.exe'):
                if Path(script).resolve() not in {p.resolve() for p in (AGGRESSIVE, AGGRESSIVE_RAR, AGGRESSIVE_7Z, PREVIEW)}:
                    raise ValueError('Encrypted archives require aggressive mode in PeelZip.')
                reply = queue.Queue(maxsize=1)
                self.events.put(('password', reply))
                password = reply.get()
                if password is None:
                    self.events.put(('cancelled', None))
                    return
                args = [*args, '--password', password]

            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
            for line in self.proc.stdout:
                self.events.put(('line', line.rstrip()))
            self.events.put(('done', self.proc.wait()))
        except Exception as e: self.events.put(('error', str(e)))

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            if messagebox.askyesno('Stop extraction?', 'Stopping may leave a partial output. Resume only according to the mode documentation.'):
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'],
                                   capture_output=True)
                else:
                    self.proc.terminate()
                self.status.set('Stopping...')

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'password':
                    password = simpledialog.askstring('Password required', 'Enter the archive password:', show='*', parent=self)
                    value.put(password)
                elif kind == 'cancelled':
                    self.proc = None
                    self._set_running(False)
                    self.status.set('Cancelled')
                elif kind == 'line':
                    if value.startswith('PEELZIP_PREVIEW '):
                        self.preview_result = json.loads(value[len('PEELZIP_PREVIEW '):])
                        self._append(space_preview.describe(self.preview_result))
                        continue
                    if value.startswith('Preview unavailable: '):
                        self.preview_error = value[len('Preview unavailable: '):]
                        self.current.set(self.preview_error)
                    self._append(value)
                    m = re.search(r'\[(\d+)/(\d+)\]\s*(.*)', value)
                    if m:
                        self.active_file = (int(m.group(1))-1, max(1,int(m.group(2))))
                        value_pct=100*self.active_file[0]/self.active_file[1]
                        self.progress.configure(value=value_pct);self.percent.set(f'{value_pct:.0f}%');self.current.set(m.group(3))
                    elif value.startswith('Bytes:'):
                        match=re.match(r'Bytes: (\d+)/(\d+) (.*)',value)
                        if match:
                            done,total=int(match[1]),max(1,int(match[2]))
                            index,count=getattr(self,'active_file',(0,1))
                            value_pct=min(99.9,100*(index+done/total)/count)
                            self.progress.configure(value=value_pct);self.percent.set(f'{value_pct:.0f}%')
                            self.current.set(f'{match[3]}  \xb7  {done/1024**2:.0f} / {total/1024**2:.0f} MiB')
                    elif value.startswith('ZIP:') or value.startswith('Estimated'):
                        self.status.set(value)
                    elif value.startswith('Complete'):
                        self.progress.configure(value=100); self.status.set(value)
                elif kind == 'done':
                    code = value; self.proc = None; self._set_running(False)
                    self.status.set('Finished successfully.' if code == 0 else f'Stopped with exit code {code}.')
                    if code == 0: self.progress.configure(value=100);self.percent.set('100%')
                    if code == 0:
                        if getattr(self, 'preview_result', None) is not None:
                            self.status.set('Space estimate ready')
                            messagebox.showinfo('Space preview', space_preview.describe(self.preview_result))
                        else:
                            messagebox.showinfo('PeelZip', 'Operation completed.')
                    elif getattr(self, 'preview_error', None):
                        self.status.set('Preview unavailable')
                        messagebox.showinfo('Preview unavailable', self.preview_error)
                elif kind == 'error':
                    self.proc = None; self._set_running(False); self.status.set('Could not start operation.'); self._append(value); messagebox.showerror('Error', value)
        except queue.Empty: pass
        self.after(100, self._poll)

    def _set_running(self, running):
        self.running = running
        if running: self.stop_btn.pack(side='right',padx=(8,0))
        else: self.stop_btn.pack_forget()
        state = 'disabled' if running else 'normal'
        self.preview_btn.configure(state=state); self.run_btn.configure(state=state); self.stop_btn.configure(state='normal' if running else 'disabled')


if __name__ == '__main__': App().mainloop()


