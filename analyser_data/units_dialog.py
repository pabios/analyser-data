"""Fenêtre « Unités… » (issue #20) : exprimer une voie dans une autre unité de la même grandeur
(A <-> mA, V <-> mV, mbar <-> bar…). Valeurs, axes, légende, exports et rapport suivent ; c'est
noté dans le journal, et reconvertir dans l'autre sens rend les valeurs d'origine."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict

from .channels import compatible_units
from .export import Key
from .i18n import _
from .theme import C, card


class UnitsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        session = app.session
        self.title(_("Unités des voies"))
        self.transient(app.root)
        self.configure(background=C["background"])

        body = ttk.Frame(self, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=_("Unités des voies"), font=app.fonts.brand).pack(anchor=tk.W)
        ttk.Label(body, text=_("Choisissez une autre unité de la même grandeur (ex. A -> mA) : les valeurs sont "
                               "converties (×1000, ÷1000…), avec les axes, la légende, les exports et le rapport. "
                               "Noté dans le journal ; reconvertir dans l'autre sens rend les valeurs d'origine."),
                  style="Muted.TLabel", wraplength=480, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 12))

        self.vars: Dict[Key, tk.StringVar] = {}
        self.current: Dict[Key, str] = {}
        entries = [(m, ch) for m in session.measurements.values() for ch in m.channels
                   if compatible_units(ch.unit) and not session.is_average((m.name, ch.label))]
        outer, box = card(body)
        outer.pack(fill=tk.BOTH, expand=True)
        rows = len(entries) + len(session.measurements)
        canvas = tk.Canvas(box, highlightthickness=0, background=C["card"], height=min(30 * rows + 10, 400))
        scroll = ttk.Scrollbar(box, orient=tk.VERTICAL, command=canvas.yview)
        table = ttk.Frame(canvas, style="Card.TFrame")
        table.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"),
                                                                width=table.winfo_reqwidth()))
        canvas.create_window((0, 0), window=table, anchor=tk.NW)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        if rows > 12:
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
        if not entries:
            ttk.Label(table, text=_("Aucune voie convertible (tension, courant, pression, puissance, résistance)."),
                      style="Muted.Card.TLabel").grid(row=0, column=0, sticky=tk.W, pady=6)
        row, last = 0, None
        for m, ch in entries:
            if m is not last:
                ttk.Label(table, text=m.name, style="Title.Card.TLabel").grid(row=row, column=0, columnspan=3,
                                                                             sticky=tk.W, pady=(6, 2))
                row, last = row + 1, m
            key = (m.name, ch.label)
            ttk.Label(table, text=ch.label, style="Card.TLabel").grid(row=row, column=0, sticky=tk.W, padx=(12, 12))
            ttk.Label(table, text=_("{} ->").format(ch.unit), style="Muted.Card.TLabel").grid(row=row, column=1,
                                                                                            sticky=tk.E)
            var = tk.StringVar(value=ch.unit)
            ttk.Combobox(table, textvariable=var, values=compatible_units(ch.unit), state="readonly", width=7).grid(
                row=row, column=2, sticky=tk.W, padx=(6, 0), pady=2)
            self.vars[key], self.current[key] = var, ch.unit
            row += 1

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text=_("Convertir"), style="Primary.TButton", command=self.apply).pack(side=tk.RIGHT)
        ttk.Button(buttons, text=_("Fermer"), command=self.destroy).pack(side=tk.RIGHT, padx=6)
        self.bind("<Return>", lambda _e: self.apply())
        self.bind("<Escape>", lambda _e: self.destroy())

    def changes(self) -> Dict[Key, str]:
        return {key: var.get() for key, var in self.vars.items() if var.get() != self.current[key]}

    def apply(self) -> None:
        changes = self.changes()
        if not changes:
            self.destroy()
            return
        try:
            self.app.convert_units(changes)
        except ValueError as exc:
            messagebox.showerror(_("Unités des voies"), str(exc), parent=self)
            return
        self.destroy()
