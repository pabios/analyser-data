"""Fenêtre « Import » : aperçu de la lecture de chaque fichier avant de l'importer (issue #10).

Pour chaque fichier : format reconnu, colonne temps, voies et unités, premières lignes lues.
On peut y choisir la feuille d'un classeur Excel, ou imposer le séparateur d'un fichier texte
si la détection automatique s'est trompée ; le fichier est alors relu. Les lectures se font en
arrière-plan : la fenêtre ne se fige jamais.
"""
from __future__ import annotations

import gc
import math
import queue
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .excel import is_excel, sheet_names
from .i18n import _, get_setting, set_setting
from .loader import SPACE, TIME_COL, LoadError
from .plotting import format_hms
from .session import legend_label
from .theme import C, card

SEPARATOR_CHOICES = {
    None: "Automatique",
    ";": "Point-virgule « ; »",
    ",": "Virgule « , »",
    "\t": "Tabulation",
    SPACE: "Espaces",
}
SEPARATOR_NAMES = {";": "« ; »", ",": "« , »", "\t": "tabulation", SPACE: "espaces"}
PREVIEW_ROWS = 8
SETTING = "import_preview"


def preview_enabled() -> bool:
    return bool(get_setting(SETTING, True))


class ImportDialog(tk.Toplevel):
    """Une ligne par fichier à gauche ; à droite, la lecture du fichier choisi."""

    def __init__(self, app, paths: List[str]):
        super().__init__(app.root)
        self.app = app
        self.paths = [Path(p) for p in paths]
        self.options: Dict[str, dict] = {str(p): {} for p in self.paths}
        self.results: Dict[str, object] = {}  # chemin -> Measurement ou message d'erreur
        self.sheets: Dict[str, List[str]] = {}
        self._jobs: Dict[str, int] = {}
        self.title(_("Import de {} fichier(s)").format(len(paths)))
        self.transient(app.root)
        self.configure(background=C["background"])
        self.geometry("{}x{}".format(min(1100, app.root.winfo_width() - 40), min(640, app.root.winfo_height() - 40)))

        body = ttk.Frame(self, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=_("Import"), font=app.fonts.brand).pack(anchor=tk.W)
        ttk.Label(body, text=_("Vérifiez la lecture de chaque fichier (colonne temps, voies, premières lignes) "
                               "avant de l'importer."), style="Muted.TLabel").pack(anchor=tk.W, pady=(0, 12))

        buttons = ttk.Frame(body)
        buttons.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))
        self.always = tk.BooleanVar(value=preview_enabled())
        ttk.Checkbutton(buttons, text=_("Toujours afficher cet aperçu à l'ouverture"), variable=self.always,
                        command=lambda: set_setting(SETTING, self.always.get())).pack(side=tk.LEFT)
        self.btn_import = ttk.Button(buttons, text=_("Importer"), style="Primary.TButton", command=self.accept)
        self.btn_import.pack(side=tk.RIGHT)
        ttk.Button(buttons, text=_("Annuler"), command=self.destroy).pack(side=tk.RIGHT, padx=6)

        panes = ttk.Frame(body)
        panes.pack(fill=tk.BOTH, expand=True)
        outer, files_box = card(panes, _("Fichiers"))
        outer.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        self.files = ttk.Treeview(files_box, show="tree", selectmode="browse", height=12)
        self.files.column("#0", width=260)
        self.files.pack(fill=tk.BOTH, expand=True)
        for p in self.paths:
            self.files.insert("", tk.END, iid=str(p), text="…  " + p.name)
        self.files.bind("<<TreeviewSelect>>", lambda _e: self._show())

        outer, self.detail = card(panes, _("Lecture du fichier"))
        outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        row = ttk.Frame(self.detail, style="Card.TFrame")
        row.pack(fill=tk.X, pady=(0, 8))
        self.sheet_label = ttk.Label(row, text=_("Feuille"), style="Card.TLabel")
        self.sheet_var = tk.StringVar()
        self.sheet_box = ttk.Combobox(row, textvariable=self.sheet_var, state="readonly", width=24)
        self.sheet_box.bind("<<ComboboxSelected>>", lambda _e: self._option_changed("sheet", self.sheet_var.get()))
        self.sep_label = ttk.Label(row, text=_("Séparateur"), style="Card.TLabel")
        self.sep_var = tk.StringVar()
        self.sep_box = ttk.Combobox(row, textvariable=self.sep_var, state="readonly", width=22,
                                    values=[_(v) for v in SEPARATOR_CHOICES.values()])
        self.sep_box.bind("<<ComboboxSelected>>", lambda _e: self._option_changed(
            "sep", next(k for k, v in SEPARATOR_CHOICES.items() if _(v) == self.sep_var.get())))
        self.info = ttk.Label(self.detail, style="Card.TLabel", justify=tk.LEFT, wraplength=700)
        self.info.pack(anchor=tk.W, fill=tk.X)
        self.table = ttk.Treeview(self.detail, show="headings", height=PREVIEW_ROWS)
        self.table.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        scroll = ttk.Scrollbar(self.detail, orient=tk.HORIZONTAL, command=self.table.xview)
        self.table.configure(xscrollcommand=scroll.set)
        scroll.pack(fill=tk.X)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.files.selection_set(str(self.paths[0]))
        for p in self.paths:
            self._read(p)

    # ------------------------------------------------------------------ lecture

    def _read(self, path: Path) -> None:
        """(Re)lit un fichier en arrière-plan avec ses réglages d'import."""
        key = str(path)
        job = self._jobs[key] = self._jobs.get(key, 0) + 1
        self.results.pop(key, None)
        self.files.item(key, text="…  " + path.name)
        self._update_import_button()
        if self.files.selection() and self.files.selection()[0] == key:
            self._show()
        messages: "queue.Queue" = queue.Queue()
        options = dict(self.options[key])

        def work():
            try:
                if is_excel(path) and key not in self.sheets:
                    messages.put(("sheets", sheet_names(path)))
                loaded, errors = self.app.session.read_files([path], options={key: options})
                messages.put(("done", loaded[0] if loaded else errors[0][1]))
            except Exception as exc:  # classeur illisible…
                messages.put(("done", str(exc)))
                traceback.print_exc()

        def poll():
            if not self.winfo_exists() or job != self._jobs[key]:
                return
            try:
                while True:
                    kind, value = messages.get_nowait()
                    if kind == "sheets":
                        self.sheets[key] = value
                        continue
                    self.results[key] = value
                    ok = not isinstance(value, str)
                    self.files.item(key, text=("✓  " if ok else "✗  ") + path.name)
                    self._update_import_button()
                    if self.files.selection() and self.files.selection()[0] == key:
                        self._show()
                    return
            except queue.Empty:
                self.after(80, poll)

        # Objets Tkinter des fenêtres fermées libérés ICI (fil principal) : sinon le ramasse-miettes
        # peut le faire dans le fil de calcul et Tkinter écrit des erreurs dans la console.
        gc.collect()
        threading.Thread(target=work, daemon=True).start()
        self.after(80, poll)

    def _option_changed(self, name: str, value) -> None:
        key = self.files.selection()[0]
        if self.options[key].get(name) != value:
            self.options[key][name] = value
            self._read(Path(key))

    # ------------------------------------------------------------------ affichage

    def _show(self) -> None:
        if not self.files.selection():
            return
        key = self.files.selection()[0]
        path = Path(key)
        for widget in (self.sheet_label, self.sheet_box, self.sep_label, self.sep_box):
            widget.pack_forget()
        if is_excel(path):
            self.sheet_label.pack(side=tk.LEFT, padx=(0, 6))
            self.sheet_box.pack(side=tk.LEFT)
            names = self.sheets.get(key, [])
            self.sheet_box.configure(values=names)
            self.sheet_var.set(self.options[key].get("sheet") or (names[0] if names else ""))
        else:
            self.sep_label.pack(side=tk.LEFT, padx=(0, 6))
            self.sep_box.pack(side=tk.LEFT)
            self.sep_var.set(_(SEPARATOR_CHOICES[self.options[key].get("sep")]))

        result = self.results.get(key)
        self.table.delete(*self.table.get_children())
        if result is None:
            self.info.configure(text=_("Lecture en cours…"), foreground=C["muted_fg"])
            self.table.configure(columns=())
            return
        if isinstance(result, str):
            self.info.configure(foreground=C["destructive"], text=result + "\n\n" + (
                _("Choisissez une autre feuille.") if is_excel(path) else
                _("Essayez un autre séparateur ci-dessus.")))
            self.table.configure(columns=())
            return
        m = result
        separator = _("Excel, feuille « {} »").format(m.sheet) if m.sheet else \
            _("séparateur {}").format(_(SEPARATOR_NAMES.get(m.separator, m.separator)))
        lines = [_("{} · {} · {} voie(s) · {} lignes · période {}").format(
            _(m.source), separator, len(m.channels), "{:,}".format(len(m.data)).replace(",", " "), m.period_label)]
        # Durée totale bien visible (issue #15) : à comparer avec la durée réelle de l'essai
        duration = format_hms(m.duration_min + (m.period_s / 60.0 if np.isfinite(m.period_s) else 0.0))
        if m.start is not None:
            end = m.start + pd.Timedelta(minutes=m.duration_min)
            lines.append(_("Durée : {} (du {} au {})").format(
                duration, m.start.strftime("%d/%m/%Y %H:%M:%S"), end.strftime("%d/%m/%Y %H:%M:%S")))
        else:
            lines.append(_("Durée : {}").format(duration))
        lines.append(_("Voies : {}").format(", ".join(legend_label(ch) for ch in m.channels)))
        lines += m.warnings
        self.info.configure(text="\n".join(lines), foreground=C["fg"])

        columns = [TIME_COL] + [ch.label for ch in m.channels]
        self.table.configure(columns=list(range(len(columns))))
        for i, ch in enumerate([None] + m.channels):
            title = _("Temps (H:MM:SS)") if ch is None else legend_label(ch)
            self.table.heading(i, text=title)
            self.table.column(i, width=130 if ch is None else 110, anchor=tk.E, stretch=False)
        fast = m.period_s < 1  # échantillonnage rapide : millisecondes affichées
        for _index, values in m.data[columns].head(PREVIEW_ROWS).iterrows():
            minutes = values[TIME_COL]
            time = format_hms(minutes) if not fast else "{}.{:03d}".format(
                format_hms(math.floor(minutes * 60) / 60), int(round(minutes * 60_000)) % 1000)
            cells = [time] + ["" if v != v else "{:.6g}".format(v) for v in values[1:]]
            self.table.insert("", tk.END, values=cells)

    def _update_import_button(self) -> None:
        ready = [m for m in self.results.values() if not isinstance(m, str)]
        pending = len(self.results) < len(self.paths)
        self.btn_import.configure(text=_("Importer {} fichier(s)").format(len(ready)))
        self.btn_import.state(["disabled"] if pending or not ready else ["!disabled"])

    # ------------------------------------------------------------------ action

    def accept(self) -> None:
        loaded = [m for m in self.results.values() if not isinstance(m, str)]
        errors = [(Path(k), v) for k, v in self.results.items() if isinstance(v, str)]
        if not loaded:
            return
        self.destroy()
        self.app._on_files_read((loaded, errors))


def needs_dialog(paths) -> Optional[bool]:
    """Aperçu obligatoire pour un classeur à plusieurs feuilles (choix de la feuille)."""
    for p in paths:
        if is_excel(p):
            try:
                if len(sheet_names(p)) > 1:
                    return True
            except Exception:  # illisible : l'import affichera l'erreur
                pass
    return None
