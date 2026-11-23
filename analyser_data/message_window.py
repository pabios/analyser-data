"""Fenêtre de message à défilement (retour client sur #19).

Les boîtes de message de Windows grandissent avec leur texte : avec 22 fichiers fusionnés, le
résumé dépassait de l'écran et les boutons n'étaient plus visibles. Ici, la fenêtre est limitée à
l'écran, le détail défile, et les boutons restent toujours visibles en bas.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import List, Optional, Sequence, Tuple

from .i18n import _
from .theme import C, bordered


class MessageWindow(tk.Toplevel):
    """``intro`` en haut (court), ``details`` dans une zone qui défile, puis les boutons.
    ``buttons`` : [(texte, valeur, principal ?)] ; fermer la fenêtre renvoie ``cancel``."""

    def __init__(self, master, title: str, intro: str, details: Sequence[str],
                 buttons: List[Tuple[str, object, bool]], cancel=None):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.configure(background=C["background"])
        self.result = cancel
        width = min(980, int(self.winfo_screenwidth() * 0.8))
        height = min(620, int(self.winfo_screenheight() * 0.7))
        self.geometry("{}x{}".format(width, height))
        self.minsize(420, 260)

        # Boutons d'abord (en bas) : ils restent visibles quelle que soit la longueur du texte
        row = ttk.Frame(self)
        row.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(8, 16))
        first = None
        for text, value, primary in reversed(buttons):
            button = ttk.Button(row, text=text, style="Primary.TButton" if primary else "TButton",
                                command=lambda v=value: self._choose(v))
            button.pack(side=tk.RIGHT, padx=(6, 0))
            if primary and first is None:
                first = button
        ttk.Label(self, text=intro, wraplength=width - 40, justify=tk.LEFT).pack(
            side=tk.TOP, fill=tk.X, padx=16, pady=(16, 8))
        if details:
            outer = bordered(self)
            outer.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=16)
            scroll = ttk.Scrollbar(outer, orient=tk.VERTICAL)
            box = tk.Text(outer, wrap=tk.WORD, relief=tk.FLAT, bd=0, padx=12, pady=8, height=8,
                          font=tkfont.nametofont("TkDefaultFont"), spacing1=2, spacing3=2,
                          background=C["card"], foreground=C["fg_soft"], highlightthickness=0,
                          yscrollcommand=scroll.set)
            scroll.configure(command=box.yview)
            box.insert("1.0", "\n".join("• " + d for d in details))
            box.configure(state=tk.DISABLED)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.protocol("WM_DELETE_WINDOW", lambda: self._choose(cancel))
        self.bind("<Escape>", lambda _e: self._choose(cancel))
        if first is not None:
            first.focus_set()

    def _choose(self, value) -> None:
        self.result = value
        self.destroy()


def _run(master, *args, **kwargs):
    window = MessageWindow(master, *args, **kwargs)
    try:
        window.grab_set()
    except tk.TclError:
        pass
    master.wait_window(window)
    return window.result


def long_info(master, title: str, intro: str, details: Sequence[str] = ()) -> None:
    """Message d'information, détail qui défile, bouton « OK » toujours visible."""
    _run(master, title, intro, details, [(_("OK"), True, True)], cancel=True)


def long_choice(master, title: str, intro: str, details: Sequence[str],
                buttons: List[Tuple[str, object, bool]], cancel=None) -> Optional[object]:
    """Question avec boutons nommés (« Conserver les trous » / « Supprimer les trous »…)."""
    return _run(master, title, intro, details, buttons, cancel=cancel)
