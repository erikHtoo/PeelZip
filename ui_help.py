"""Small keyboard-, hover- and click-accessible help buttons."""
import tkinter as tk


HELP = {
    'aggressive': 'Reclaims source space during extraction where supported. ZIP can reclaim within a file; other formats may wait for a complete file or compression group. Source data is consumed. An interrupted run may require a new download.',
    'normal': 'For ZIP, extracts and verifies each complete file before shrinking the source. Needs room for the current file and consumes the ZIP. Other supported formats use ordinary extraction and keep the source.',
    'verify': 'Adds an extra CRC check of the output. It may slow extraction but does not reduce space savings. Streaming ZIP still checks CRC while decoding; the extra check cannot restore source bytes already reclaimed.',
    'resume': 'Uses the existing journal in the same destination to skip completed work. Incomplete streaming ZIP files cannot resume. RAR and 7z recovery is limited. Do not use an old journal with a new download.',
    'password': 'Enter the archive password if it is encrypted. Used in aggressive mode. The field and activity log hide it, but local process-inspection tools may see it.',
    'preview': 'Estimates space for the conservative ZIP workflow without extracting. Currently unavailable for aggressive mode and other formats.',
    'log': 'Shows detailed progress and error messages. Useful for understanding why extraction stopped.',
}


class HelpButton(tk.Button):
    def __init__(self, parent, text, background='#111512'):
        super().__init__(parent, text='?', command=self.show,
                         bg=background, fg='#9ca89d', activebackground='#29322b',
                         activeforeground='#d6f578', relief='flat', borderwidth=0,
                         highlightthickness=1, highlightbackground='#38433a',
                         highlightcolor='#d6f578', width=2, cursor='hand2',
                         font=('Segoe UI', 9, 'bold'), takefocus=True)
        self.explanation = text
        self.popup = None
        self.timer = None
        self.bind('<Enter>', self.schedule)
        self.bind('<FocusIn>', self.schedule)
        self.bind('<Leave>', self.hide)
        self.bind('<FocusOut>', self.hide)
        self.bind('<Escape>', self.hide)
        self.bind('<Return>', self.show)
        self.bind('<Destroy>', self.hide)

    def schedule(self, event=None):
        self.hide()
        self.timer = self.after(350, self.show)

    def show(self, event=None):
        self.hide()
        popup = self.popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.attributes('-topmost', True)
        tk.Label(popup, text=self.explanation, wraplength=320, justify='left',
                 bg='#29322b', fg='#edf1e9', padx=14, pady=12,
                 font=('Segoe UI', 10)).pack()
        popup.update_idletasks()
        x = min(self.winfo_rootx(), self.winfo_screenwidth() - popup.winfo_reqwidth() - 12)
        y = self.winfo_rooty() + self.winfo_height() + 6
        if y + popup.winfo_reqheight() > self.winfo_screenheight() - 40:
            y = self.winfo_rooty() - popup.winfo_reqheight() - 6
        popup.geometry(f'+{max(0,x)}+{max(0,y)}')
        popup.deiconify()

    def hide(self, event=None):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        if self.popup is not None:
            popup, self.popup = self.popup, None
            popup.destroy()
