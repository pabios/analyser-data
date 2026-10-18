"""Fenêtre « Corrections par sonde » (issue #11) : une correction propre à chaque voie °C / %HR.

Champ vide = la voie suit la correction générale de sa grandeur (carte Enceinte climatique).
Valeur affichée = valeur mesurée + correction ; les mesures d'origine ne changent jamais.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict

from .channels import HUMIDITY, TEMPERATURE
from .export import Key
from .i18n import _
from .session import format_offset, legend_label, parse_temperature_offset
from .theme import C, card


class ProbeCorrectionsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        session = app.session
        self.title(_("Corrections par sonde"))
        self.transient(app.root)
        self.configure(background=C["background"])

        body = ttk.Frame(self, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=_("Corrections par sonde"), font=app.fonts.brand).pack(anchor=tk.W)
        ttk.Label(body, text=_("Valeur affichée = valeur mesurée + correction (-5,0 à +5,0, pas de 0,1). "
                               "Champ vide : la sonde suit la correction générale."),
                  style="Muted.TLabel").pack(anchor=tk.W, pady=(0, 12))

        outer, box = card(body)
        outer.pack(fill=tk.BOTH, expand=True)
        self.vars: Dict[Key, tk.StringVar] = {}
        probes = [(m.name, ch) for m in session.measurements.values() for ch in m.channels
                  if ch.quantity in (TEMPERATURE, HUMIDITY)]
        if not probes:
            ttk.Label(box, text=_("Aucune voie de température ou d'humidité dans les fichiers ouverts."),
                      style="Card.TLabel").pack(anchor=tk.W)
        headers = [_("VOIE"), _("FICHIER"), _("CORRECTION"), _("GÉNÉRALE")]
        table = ttk.Frame(box, style="Card.TFrame")
        table.pack(fill=tk.BOTH, expand=True)
        if probes:
            for col, text in enumerate(headers):
                ttk.Label(table, text=text, style="Muted.Card.TLabel").grid(row=0, column=col, sticky=tk.W,
                                                                            padx=6, pady=(0, 6))
        temperatures = [(n, ch) for n, ch in probes if ch.quantity == TEMPERATURE]
        for row, (name, ch) in enumerate(probes, start=1):
            key = (name, ch.label)
            number = temperatures.index((name, ch)) + 1 if len(temperatures) > 1 and ch.quantity == TEMPERATURE else 0
            ttk.Label(table, text="{} — {}".format(legend_label(ch, number), ch.label), style="Card.TLabel").grid(
                row=row, column=0, sticky=tk.W, padx=6, pady=2)
            ttk.Label(table, text=name, style="Muted.Card.TLabel").grid(row=row, column=1, sticky=tk.W, padx=6)
            cell = ttk.Frame(table, style="Card.TFrame")
            cell.grid(row=row, column=2, sticky=tk.W, padx=6)
            var = tk.StringVar(value=format_offset(session.channel_offsets[key])
                               if key in session.channel_offsets else "")
            ttk.Entry(cell, textvariable=var, width=7, justify=tk.RIGHT).pack(side=tk.LEFT)
            ttk.Label(cell, text=ch.unit, style="Muted.Card.TLabel").pack(side=tk.LEFT, padx=(4, 0))
            general = session.temperature_offset if ch.quantity == TEMPERATURE else session.humidity_offset
            ttk.Label(table, text="{} {}".format(format_offset(general), ch.unit), style="Muted.Card.TLabel").grid(
                row=row, column=3, sticky=tk.W, padx=6)
            self.vars[key] = var

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text=_("Tout effacer"), command=lambda: [v.set("") for v in self.vars.values()]).pack(
            side=tk.LEFT)
        ttk.Button(buttons, text=_("Appliquer"), style="Primary.TButton", command=self.apply).pack(side=tk.RIGHT)
        ttk.Button(buttons, text=_("Annuler"), command=self.destroy).pack(side=tk.RIGHT, padx=6)
        self.bind("<Return>", lambda _e: self.apply())
        self.bind("<Escape>", lambda _e: self.destroy())

    def values(self) -> Dict[Key, object]:
        """Corrections saisies (None : valeur générale). ValueError si une saisie est invalide."""
        out = {}
        for key, var in self.vars.items():
            text = var.get().strip()
            unit = self.app.session.measurements[key[0]].channel(key[1]).unit
            out[key] = parse_temperature_offset(text, unit) if text else None
        return out

    def apply(self) -> None:
        try:
            values = self.values()
        except ValueError as exc:
            messagebox.showerror(_("Corrections par sonde"), str(exc), parent=self)
            return
        self.destroy()
        self.app.apply_probe_corrections(values)
