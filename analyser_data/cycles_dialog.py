"""Fenêtre « Cycles » (issue #12 : un essai plus long) : dupliquer ou retirer des cycles entiers.

Les cycles sont détectés sur une voie de référence et numérotés. Toutes les voies du fichier
sont modifiées aux mêmes points, au début exact d'un cycle. L'aperçu montre le signal et son
spectre (FFT) avant / après : la fréquence fondamentale f0 doit rester la même. Les cycles
ajoutés ne sont pas des mesures : ils sont signalés dans le journal, le CSV et le rapport
(pas de hachure sur le graphique, à la demande du client).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .channels import CURRENT
from .cycles import Edit, spectrum
from .export import Key
from .help import HelpWindow
from .i18n import _
from .loader import TIME_COL
from .plotting import format_hms
from .theme import C, card

PREVIEW_POINTS = 4000


def _mhz(f: Optional[float]) -> str:
    return "—" if not f else "{:.4g} mHz".format(f * 1000).replace(".", ",")


class CyclesDialog(tk.Toplevel):
    def __init__(self, app, keys: List[Key]):
        super().__init__(app.root)
        self.app = app
        self.session = app.session
        self.title(_("Cycles"))
        self.transient(app.root)
        self.configure(background=C["background"])
        self.geometry("{}x{}".format(min(1180, app.root.winfo_width() - 20), min(720, app.root.winfo_height() - 20)))
        self.cycles = []
        self.preview = None

        body = ttk.Frame(self, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=_("Cycles — essai plus long"), font=app.fonts.brand).pack(anchor=tk.W)
        ttk.Label(body, text=_("Dupliquez ou retirez des cycles entiers. Toutes les voies du fichier sont modifiées "
                               "aux mêmes points, au début exact d'un cycle ; le signal et le temps restent continus. "
                               "Les cycles ajoutés ne sont pas des mesures : ils sont signalés dans le journal, "
                               "le CSV et le rapport."),
                  style="Muted.TLabel", wraplength=1100, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 12))

        buttons = ttk.Frame(body)
        buttons.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="?", style="Icon.TButton", width=2,
                   command=lambda: HelpWindow.open(self, "Cycles")).pack(side=tk.LEFT)
        self.summary = tk.StringVar()
        ttk.Label(buttons, textvariable=self.summary, style="Muted.TLabel").pack(side=tk.LEFT, padx=10)
        self.btn_apply = ttk.Button(buttons, text=_("Appliquer"), style="Primary.TButton", command=self.apply)
        self.btn_apply.pack(side=tk.RIGHT)
        ttk.Button(buttons, text=_("Annuler"), command=self.destroy).pack(side=tk.RIGHT, padx=6)
        ttk.Button(buttons, text=_("Aperçu"), command=self.update_preview).pack(side=tk.RIGHT)

        panes = ttk.Frame(body)
        panes.pack(fill=tk.BOTH, expand=True)
        outer, left = card(panes, _("Cycles détectés"))
        outer.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))

        # Voie de référence : celle qui définit les cycles (ex. le courant)
        channels = [(m.name, c) for m in self.session.measurements.values() for c in m.channels]
        self.choices = {"{} — {}".format(c.label, n): (n, c.label) for n, c in channels}
        default = next((k for k in keys if self.session.measurements[k[0]].channel(k[1]).quantity == CURRENT),
                       keys[0] if keys else (channels[0][0], channels[0][1].label))
        ttk.Label(left, text=_("Voie de référence"), style="Card.TLabel").pack(anchor=tk.W)
        self.ref_var = tk.StringVar(value="{} — {}".format(default[1], default[0]))
        ref = ttk.Combobox(left, textvariable=self.ref_var, values=list(self.choices), state="readonly", width=38)
        ref.pack(anchor=tk.W, fill=tk.X, pady=(2, 8))
        ref.bind("<<ComboboxSelected>>", lambda _e: self.detect())
        self.table = ttk.Treeview(left, columns=("n", "start", "length", "min", "max"), show="headings", height=10)
        for col, text, width in (("n", _("N°"), 40), ("start", _("Début"), 80), ("length", _("Durée"), 70),
                                 ("min", "MIN", 70), ("max", "MAX", 70)):
            self.table.heading(col, text=text)
            self.table.column(col, width=width, anchor=tk.E)
        self.table.pack(fill=tk.BOTH, expand=True)
        self.table.bind("<<TreeviewSelect>>", self._row_selected)

        # Opération
        ops = ttk.Frame(left, style="Card.TFrame")
        ops.pack(fill=tk.X, pady=(10, 0))
        self.kind = tk.StringVar(value="duplicate")
        self.source, self.count, self.after = tk.StringVar(value="1"), tk.StringVar(value="1"), tk.StringVar(value="0")
        self.first, self.last = tk.StringVar(value="1"), tk.StringVar(value="1")
        row = ttk.Frame(ops, style="Card.TFrame")
        row.pack(fill=tk.X)
        ttk.Radiobutton(row, text=_("Dupliquer le cycle"), variable=self.kind, value="duplicate",
                        command=self.update_preview).pack(side=tk.LEFT)
        self._spin(row, self.source)
        self._spin(row, self.count, to=500)
        ttk.Label(row, text=_("fois"), style="Card.TLabel").pack(side=tk.LEFT, padx=4)
        row = ttk.Frame(ops, style="Card.TFrame")
        row.pack(fill=tk.X, padx=(22, 0), pady=(2, 6))
        ttk.Label(row, text=_("insérés après le cycle (0 = au début)"), style="Card.TLabel").pack(side=tk.LEFT)
        self._spin(row, self.after, low=0)
        row = ttk.Frame(ops, style="Card.TFrame")
        row.pack(fill=tk.X)
        ttk.Radiobutton(row, text=_("Retirer les cycles"), variable=self.kind, value="remove",
                        command=self.update_preview).pack(side=tk.LEFT)
        self._spin(row, self.first)
        ttk.Label(row, text=_("à"), style="Card.TLabel").pack(side=tk.LEFT, padx=4)
        self._spin(row, self.last)

        # Aperçu : signal et spectre avant / après
        outer, right = card(panes, _("Aperçu (rien n'est modifié avant « Appliquer »)"))
        outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.check = tk.StringVar()
        ttk.Label(right, textvariable=self.check, style="Card.TLabel", wraplength=640, justify=tk.LEFT).pack(
            anchor=tk.W)
        self.figure = Figure(figsize=(6.5, 4.6), dpi=100, facecolor=C["card"])
        self.figure.set_layout_engine("constrained")
        self.canvas = FigureCanvasTkAgg(self.figure, master=right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.detect()

    def _spin(self, parent, var, low=1, to=9999):
        box = ttk.Spinbox(parent, from_=low, to=to, width=5, textvariable=var, command=self.update_preview)
        box.pack(side=tk.LEFT, padx=(6, 0))
        box.bind("<Return>", lambda _e: self.update_preview())
        return box

    # ------------------------------------------------------------------ données

    @property
    def key(self) -> Key:
        return self.choices[self.ref_var.get()]

    def detect(self) -> None:
        """Cycles de la voie de référence, numérotés dans le tableau."""
        self.table.delete(*self.table.get_children())
        self.cycles = self.session.detect_cycles(self.key)
        m = self.session.measurements[self.key[0]]
        t = m.data[TIME_COL].to_numpy(dtype=float)
        y = m.data[self.key[1]].to_numpy(dtype=float)
        for c in self.cycles:
            part = y[c.start:c.stop]
            self.table.insert("", tk.END, iid=str(c.number), values=(
                c.number, format_hms(t[c.start]), format_hms((c.stop - c.start) * m.period_s / 60.0),
                "{:.4g}".format(np.nanmin(part)), "{:.4g}".format(np.nanmax(part))))
        if self.cycles:
            last = self.cycles[-1].number
            self.source.set("1" if last < 2 else "2")
            self.after.set(str(last))  # par défaut : à la fin de l'essai
            self.first.set(str(last))
            self.last.set(str(last))
        self.update_preview()

    def _row_selected(self, _event=None) -> None:
        selected = self.table.selection()
        if selected:
            (self.source if self.kind.get() == "duplicate" else self.first).set(selected[0])
            if self.kind.get() == "remove":
                self.last.set(selected[-1])
            self.update_preview()

    def edit(self) -> Edit:
        def number(var):
            return int(str(var.get()).strip())

        if self.kind.get() == "remove":
            return Edit("remove", first=number(self.first), last=number(self.last))
        return Edit("duplicate", source=number(self.source), count=number(self.count), after=number(self.after))

    # ------------------------------------------------------------------ aperçu

    def update_preview(self) -> None:
        self.figure.clear()
        self.preview = None
        if not self.cycles:
            self.check.set(_("Aucun cycle détecté sur cette voie : choisissez une autre voie de référence "
                             "(par exemple le courant)."))
            self.summary.set("")
            self.btn_apply.state(["disabled"])
            self.canvas.draw_idle()
            return
        try:
            preview = self.session.preview_cycle_edit(self.key, self.edit())
        except ValueError:
            self.check.set(_("Numéros de cycles invalides : choisissez des cycles entre 1 et {}.").format(
                len(self.cycles)))
            self.btn_apply.state(["disabled"])
            self.canvas.draw_idle()
            return
        self.preview = preview
        self.btn_apply.state(["!disabled"])
        step_min = preview["step_s"] / 60.0
        before, after = preview["before"], preview["after"]

        ax = self.figure.add_subplot(211)
        for y, color, label in ((before, C["border_strong"], _("Avant")), (after, "#1f5fd6", _("Après"))):
            stride = max(1, len(y) // PREVIEW_POINTS)
            ax.plot(np.arange(0, len(y), stride) * step_min, y[::stride], color=color, linewidth=0.9, label=label)
        for j in preview["junctions"]:
            ax.axvline(j * step_min, color="#d97706", linewidth=0.8, linestyle="--")
        ax.set_xlabel(_("Temps écoulé (min)"), fontsize=8)
        ax.legend(fontsize=7, loc="upper right")
        ax.tick_params(labelsize=7)

        ax = self.figure.add_subplot(212)
        for y, color, label in ((before, C["border_strong"], _("Spectre avant")), (after, "#1f5fd6", _("Spectre après"))):
            freq, amp = spectrum(y, preview["step_s"])
            limit = 10 * (preview["f0_before"] or freq[-1])
            keep = (freq > 0) & (freq <= limit)
            ax.semilogy(freq[keep] * 1000, np.maximum(amp[keep], 1e-6), color=color, linewidth=0.9, label=label)
        if preview["f0_before"]:
            ax.axvline(preview["f0_before"] * 1000, color="#2e9e44", linewidth=0.8, linestyle=":")
        ax.set_xlabel(_("Fréquence (mHz)"), fontsize=8)
        ax.legend(fontsize=7, loc="upper right")
        ax.tick_params(labelsize=7)
        self.canvas.draw_idle()

        f0a, f0b = preview["f0_before"], preview["f0_after"]
        gap = abs(f0b - f0a) / f0a * 100 if f0a and f0b else None
        ok = gap is not None and gap < 2.0
        m = self.session.measurements[self.key[0]]
        duration = len(before) * m.period_s / 60.0
        new_duration = len(after) * m.period_s / 60.0
        self.check.set(_("Fréquence fondamentale f0 : avant {} → après {} ({}) · {} cycles → {} · durée {} → {}").format(
            _mhz(f0a), _mhz(f0b),
            (_("écart {:.1f} %, forme conservée ✓").format(gap) if ok else _("écart {:.1f} % ⚠ à vérifier").format(gap))
            if gap is not None else "—",
            len(preview["cycles_before"]), len(preview["cycles_after"]), format_hms(duration), format_hms(new_duration)))
        if preview["added_points"]:
            self.summary.set(_("{} points ajoutés (NON MESURÉS) sur toutes les voies de {}").format(
                preview["added_points"], self.key[0]))
        else:
            self.summary.set(_("{} points retirés sur toutes les voies de {}").format(
                preview["removed_points"], self.key[0]))

    def apply(self) -> None:
        try:
            edit = self.edit()
            self.session.preview_cycle_edit(self.key, edit)
        except ValueError:
            messagebox.showerror(_("Cycles"), _("Numéros de cycles invalides."), parent=self)
            return
        key = self.key
        self.destroy()
        self.app.apply_cycle_edit(key, edit)
