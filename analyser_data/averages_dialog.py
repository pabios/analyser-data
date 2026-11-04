"""Fenêtre « Moyenne de voies » (issue #16) : cocher au moins deux voies de même unité pour
afficher leur moyenne point par point, comme une voie de plus (« Moyenne Channel 1 / Channel 2 »).

La moyenne est une voie calculée : les mesures d'origine ne changent pas, et elle se recalcule
quand ses voies sont nettoyées ou corrigées. Elle peut être retirée à tout moment.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict

from .export import Key
from .i18n import _
from .theme import C, card


class AveragesDialog(tk.Toplevel):
    def __init__(self, app, preselected=()):
        super().__init__(app.root)
        self.app = app
        session = app.session
        self.title(_("Moyenne de voies"))
        self.transient(app.root)
        self.configure(background=C["background"])

        body = ttk.Frame(self, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=_("Moyenne de voies"), font=app.fonts.brand).pack(anchor=tk.W)
        ttk.Label(body, text=_("Cochez au moins deux voies de même unité : leur moyenne point par point "
                               "s'ajoute comme une nouvelle voie. Les mesures d'origine ne changent pas."),
                  style="Muted.TLabel", wraplength=460, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 12))

        outer, box = card(body)
        outer.pack(fill=tk.BOTH, expand=True)
        rows = sum(len(m.channels) + 1 for m in session.measurements.values())
        canvas = tk.Canvas(box, highlightthickness=0, background=C["card"], height=min(26 * rows + 10, 380))
        scroll = ttk.Scrollbar(box, orient=tk.VERTICAL, command=canvas.yview)
        table = ttk.Frame(canvas, style="Card.TFrame")
        table.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"),
                                                                width=table.winfo_reqwidth()))
        canvas.create_window((0, 0), window=table, anchor=tk.NW)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        if rows > 14:
            scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.vars: Dict[Key, tk.BooleanVar] = {}
        preselected = {tuple(k) for k in preselected}
        row = 0
        for m in session.measurements.values():
            ttk.Label(table, text=m.name, style="Title.Card.TLabel").grid(row=row, column=0, columnspan=2,
                                                                         sticky=tk.W, pady=(6, 2))
            row += 1
            for ch in m.channels:
                key = (m.name, ch.label)
                if session.is_average(key):
                    continue
                var = tk.BooleanVar(value=key in preselected)
                ttk.Checkbutton(table, text=ch.label, variable=var, style="Card.TCheckbutton").grid(
                    row=row, column=0, sticky=tk.W, padx=(12, 6))
                ttk.Label(table, text=ch.unit or "—", style="Muted.Card.TLabel").grid(row=row, column=1, sticky=tk.W)
                self.vars[key] = var
                row += 1

        # Moyennes déjà affichées : on peut les retirer
        if session.averages:
            ttk.Label(body, text=_("Moyennes affichées"), font=self.app.fonts.strong).pack(anchor=tk.W, pady=(12, 4))
            for key in list(session.averages):
                line = ttk.Frame(body)
                line.pack(fill=tk.X, pady=1)
                ttk.Label(line, text="{} [{}]".format(key[1], key[0])).pack(side=tk.LEFT)
                ttk.Button(line, text=_("Retirer"), style="Small.TButton",
                           command=lambda k=key: self.remove(k)).pack(side=tk.RIGHT)

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text=_("Afficher la moyenne"), style="Primary.TButton", command=self.apply).pack(
            side=tk.RIGHT)
        ttk.Button(buttons, text=_("Fermer"), command=self.destroy).pack(side=tk.RIGHT, padx=6)
        self.bind("<Return>", lambda _e: self.apply())
        self.bind("<Escape>", lambda _e: self.destroy())

    def chosen(self):
        return [key for key, var in self.vars.items() if var.get()]

    def apply(self) -> None:
        try:
            self.app.add_average(self.chosen())
        except ValueError as exc:
            messagebox.showerror(_("Moyenne de voies"), str(exc), parent=self)
            return
        self.destroy()

    def remove(self, key) -> None:
        self.destroy()
        self.app.remove_average(key)
