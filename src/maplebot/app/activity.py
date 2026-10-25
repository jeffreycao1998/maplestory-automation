"""Live log viewer. All methods are called only by the Tk thread."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


class ActivityWindow(tk.Toplevel):
    def __init__(self,master,journal,copy_diagnostics,open_logs,pause_key):
        super().__init__(master)
        self.title('MapleBot activity log')
        ttk.Label(self,text='Opening app windows during a run can pause game input. Return to the game and press '
                  +pause_key.upper()+' to resume.',wraplength=700,padding=8).pack(fill='x')
        self.text=ScrolledText(self,width=95,height=18,wrap='word',state='disabled')
        self.text.pack(fill='both',expand=True,padx=8,pady=4)
        self.text.config(state='normal');self.text.insert('end',journal.text()+'\n');self.text.config(state='disabled')
        self.text.see('end')
        row=ttk.Frame(self,padding=8);row.pack(fill='x')
        ttk.Button(row,text='Copy diagnostics',command=copy_diagnostics).pack(side='left')
        ttk.Button(row,text='Open logs folder',command=open_logs).pack(side='left',padx=8)
        ttk.Button(row,text='Close',command=self.destroy).pack(side='right')
        self.max_lines=journal.lines.maxlen

    def append(self,line):
        # Do not yank the scrollbar away when the user is reading older entries.
        follow=self.text.yview()[1]>=.99
        self.text.config(state='normal')
        self.text.insert('end',line+'\n')
        count=int(self.text.index('end-1c').split('.')[0])-1
        if count>self.max_lines:self.text.delete('1.0',f'{count-self.max_lines+1}.0')
        self.text.config(state='disabled')
        if follow:self.text.see('end')
