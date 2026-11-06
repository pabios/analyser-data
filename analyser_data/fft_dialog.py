"""Fenêtre « Spectre FFT » (issue #16) : spectre d'amplitude des voies choisies.

Calcul sur les voies telles qu'affichées (nettoyage et corrections compris), sur la zone
affichée du graphique (zoom) ou sur tout l'essai. Un graphique par unité, un tableau des pics
principaux, export CSV du spectre et image. Rien n'est modifié dans les mesures.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .export import Key, write_csv
from .fft import format_frequency, format_period
from .i18n import _
from .plotting import decimate_indices, format_hms
from .theme import C, card

PERIODS = {"view": "Zone affichée (zoom)", "all": "Tout l'essai"}
MAX_UNITS = 4  # graphiques empilés au plus (une unité chacun)


class FftDialog(tk.Toplevel):
    def __init__(self, app, keys: List[Key], view=None):
        super().__init__(app.root)
        self.app = app
        self.session = app.session
        self.view = view
        self.spectra: Dict[Key, object] = {}
        self.title(_("Spectre FFT"))
        self.transient(app.root)
        self.configure(background=C["background"])
        self.geometry("980x720")

        body = ttk.Frame(self, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=_("Spectre FFT"), font=app.fonts.brand).pack(anchor=tk.W)
        ttk.Label(body, text=_("Spectre d'amplitude des voies choisies, telles qu'affichées (nettoyage et "
                               "corrections compris). Moyenne retirée, fenêtre de Hann : une sinusoïde "
                               "d'amplitude A donne un pic de hauteur A. Les mesures ne sont pas modifiées."),
                  style="Muted.TLabel", wraplength=900, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 10))

        panes = ttk.Frame(body)
        panes.pack(fill=tk.BOTH, expand=True)
        outer, left = card(panes, _("Voies"))
        outer.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        self.vars: Dict[Key, tk.BooleanVar] = {}
        chosen = {tuple(k) for k in keys}
        listing = ttk.Frame(left, style="Card.TFrame")
        listing.pack(fill=tk.BOTH, expand=True)
        rows = sum(len(m.channels) + 1 for m in self.session.measurements.values())
        canvas = tk.Canvas(listing, highlightthickness=0, background=C["card"], height=min(24 * rows + 10, 330),
                           width=230)
        scroll = ttk.Scrollbar(listing, orient=tk.VERTICAL, command=canvas.yview)
        table = ttk.Frame(canvas, style="Card.TFrame")
        table.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=table, anchor=tk.NW)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        if rows > 13:
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
        row = 0
        for m in self.session.measurements.values():
            ttk.Label(table, text=m.name, style="Strong.Card.TLabel").grid(row=row, column=0, sticky=tk.W, pady=(4, 0))
            row += 1
            for ch in m.channels:
                key = (m.name, ch.label)
                var = tk.BooleanVar(value=key in chosen)
                ttk.Checkbutton(table, text=ch.label, variable=var, style="Card.TCheckbutton",
                                command=self.update).grid(row=row, column=0, sticky=tk.W, padx=(8, 0))
                self.vars[key] = var
                row += 1

        ttk.Label(left, text=_("Période analysée"), style="Strong.Card.TLabel").pack(anchor=tk.W, pady=(10, 2))
        self.period = tk.StringVar(value=_(PERIODS["view" if view is not None else "all"]))
        box = ttk.Combobox(left, textvariable=self.period, values=[_(v) for v in PERIODS.values()], state="readonly",
                           width=24)
        box.pack(anchor=tk.W)
        box.bind("<<ComboboxSelected>>", lambda _e: self.update())
        self.log_x = tk.BooleanVar(value=False)
        self.log_y = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text=_("Fréquences en échelle log"), variable=self.log_x, style="Card.TCheckbutton",
                        command=self.redraw).pack(anchor=tk.W, pady=(10, 0))
        ttk.Checkbutton(left, text=_("Amplitudes en échelle log"), variable=self.log_y, style="Card.TCheckbutton",
                        command=self.redraw).pack(anchor=tk.W)

        outer, right = card(panes)
        outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.figure = Figure(figsize=(6.8, 4.2), dpi=100, facecolor=C["card"])
        self.figure.set_layout_engine("constrained")
        self.canvas = FigureCanvasTkAgg(self.figure, master=right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        columns = ("voie", "pic", "periode", "amplitude", "moyenne", "points")
        self.table = ttk.Treeview(right, columns=columns, show="headings", height=5)
        for col, text, width in zip(columns, (_("Voie"), _("Pic principal"), _("Période du pic"), _("Amplitude"),
                                              _("Moyenne"), _("Points analysés")), (150, 95, 90, 95, 85, 130)):
            self.table.heading(col, text=text)
            self.table.column(col, width=width, stretch=col == "voie")
        self.table.pack(fill=tk.X, pady=(8, 0))
        self.info = tk.StringVar()
        ttk.Label(right, textvariable=self.info, style="Muted.Card.TLabel", wraplength=700, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(6, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text=_("Exporter le spectre (CSV)…"), command=self.export_csv).pack(side=tk.LEFT)
        ttk.Button(buttons, text=_("Image du spectre…"), command=self.export_image).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text=_("Fermer"), style="Primary.TButton", command=self.destroy).pack(side=tk.RIGHT)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.update()

    # ------------------------------------------------------------------ calcul

    def chosen(self) -> List[Key]:
        return [key for key, var in self.vars.items() if var.get()]

    def current_view(self):
        return self.view if self.period.get() == _(PERIODS["view"]) else None

    def update(self) -> None:
        """Recalcule les spectres des voies cochées."""
        self.spectra, errors = {}, []
        view = self.current_view()
        for key in self.chosen():
            try:
                self.spectra[key] = self.session.spectrum(key, view)
            except ValueError as exc:
                errors.append("{} : {}".format(key[1], exc))
        self.table.delete(*self.table.get_children())
        for key, sp in self.spectra.items():
            unit = self.session.measurements[key[0]].channel(key[1]).unit
            filled = _(" dont {} comblés").format(sp.filled) if sp.filled else ""
            self.table.insert("", tk.END, values=(
                key[1], format_frequency(sp.peak_hz), format_period(sp.peak_period_s),
                _amount(sp.peak_amplitude, unit), _amount(sp.mean, unit),
                "{:,}".format(sp.points).replace(",", " ") + filled))
        lines = []
        if self.spectra:
            first = next(iter(self.spectra.values()))
            span = format_hms(first.points * first.step_s / 60.0)
            lines.append(_("Durée analysée : {} · pas {} · résolution {}").format(
                span, format_period(first.step_s), format_frequency(first.resolution_hz)))
        if any(sp.filled for sp in self.spectra.values()):
            lines.append(_("Des points manquants (trous, interruptions) ont été comblés par interpolation "
                           "pour le calcul : choisissez une zone sans interruption pour un spectre exact."))
        lines += errors
        if not self.chosen():
            lines.append(_("Cochez au moins une voie."))
        self.info.set("\n".join(lines))
        self.redraw()

    def redraw(self) -> None:
        self.figure.clear()
        if not self.spectra:
            self.canvas.draw_idle()
            return
        units: Dict[str, List[Key]] = {}
        for key in self.spectra:
            unit = self.session.measurements[key[0]].channel(key[1]).unit
            units.setdefault(unit, []).append(key)
        shown = list(units.items())[:MAX_UNITS]
        axes = self.figure.subplots(len(shown), 1, sharex=True, squeeze=False)[:, 0]
        for ax, (unit, keys) in zip(axes, shown):
            for key in keys:
                sp = self.spectra[key]
                freq, amp = sp.freq[1:], sp.amplitude[1:]
                index = decimate_indices(amp, 0, len(amp))
                color = self.app._key_color.get(key)
                (line,) = ax.plot(freq[index], amp[index], linewidth=0.9, color=color, label=key[1])
                if sp.peak_hz:
                    ax.plot([sp.peak_hz], [sp.peak_amplitude], "o", markersize=4, color=line.get_color())
            ax.set_ylabel(_("Amplitude ({})").format(unit) if unit else _("Amplitude"), fontsize=8)
            if self.log_y.get():
                ax.set_yscale("log")
            if self.log_x.get():
                ax.set_xscale("log")
            ax.grid(True, color=C["muted"], linewidth=0.8)
            ax.tick_params(labelsize=8)
            ax.legend(fontsize=7, loc="upper right")
        axes[-1].set_xlabel(_("Fréquence (Hz)"), fontsize=8)
        if len(units) > MAX_UNITS:
            self.info.set(self.info.get() + "\n" + _("Plus de {} unités : seules les {} premières sont tracées.").format(
                MAX_UNITS, MAX_UNITS))
        self.canvas.draw_idle()

    # ------------------------------------------------------------------ exports

    def spectrum_table(self) -> pd.DataFrame:
        """Une paire de colonnes (fréquence, amplitude) par voie."""
        columns = {}
        for key, sp in self.spectra.items():
            unit = self.session.measurements[key[0]].channel(key[1]).unit
            columns[_("{} | Fréquence (Hz)").format(key[1])] = pd.Series(sp.freq)
            columns[_("{} | Amplitude ({})").format(key[1], unit or "-")] = pd.Series(sp.amplitude)
        return pd.DataFrame(columns)

    def export_csv(self) -> Optional[Path]:
        if not self.spectra:
            messagebox.showinfo(_("Spectre FFT"), _("Cochez au moins une voie."), parent=self)
            return None
        path = filedialog.asksaveasfilename(parent=self, title=_("Exporter le spectre"), defaultextension=".csv",
                                            initialfile=_("spectre_fft.csv"), filetypes=[("CSV", "*.csv")])
        if not path:
            return None
        write_csv(self.spectrum_table(), path)
        self.session.log(_("Spectre FFT exporté : {} ({}, {})").format(
            Path(path).name, ", ".join(k[1] for k in self.spectra), self.period.get()))
        self.app._set_status(_("Spectre FFT exporté : {}").format(path))
        return Path(path)

    def export_image(self) -> Optional[Path]:
        if not self.spectra:
            messagebox.showinfo(_("Spectre FFT"), _("Cochez au moins une voie."), parent=self)
            return None
        path = filedialog.asksaveasfilename(parent=self, title=_("Image du spectre"), defaultextension=".png",
                                            initialfile=_("spectre_fft.png"),
                                            filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")])
        if not path:
            return None
        self.figure.savefig(path, dpi=200, facecolor="white")
        self.session.log(_("Image du spectre FFT exportée : {}").format(Path(path).name))
        self.app._set_status(_("Image du spectre exportée : {}").format(path))
        return Path(path)


def _amount(value: Optional[float], unit: str) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    from .fft import _number

    return _number("{:.4g}".format(value)) + (" " + unit if unit else "")
