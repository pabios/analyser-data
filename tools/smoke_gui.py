"""Test de bout en bout de l'interface (exécuté dans Docker, sur l'écran virtuel Xvfb).

Ouvre les fichiers d'exemple, nettoie, corrige un point au clic, décale la température,
exporte, et enregistre des captures d'écran dans le dossier passé en argument.

Usage : ./dev.sh smoke   (captures dans .dev-out/)
        python tools/smoke_gui.py <dossier> <gros_fichier.csv>   (teste aussi un gros fichier)
"""
import sys
import time
import tkinter as tk
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from analyser_data import app as app_module  # noqa: E402
from analyser_data.import_dialog import ImportDialog  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".dev-out")
OUT.mkdir(parents=True, exist_ok=True)
EXAMPLES = [str(ROOT / "exemples" / f) for f in ("GL980_Mes-_260601-170139.CSV", "nanodac_Rd_Z.txt")]

# Les boîtes de dialogue bloqueraient le test : on les remplace.
dialogs = []
app_module.messagebox.showinfo = lambda *a, **k: dialogs.append(("info",) + a)
app_module.messagebox.showwarning = lambda *a, **k: dialogs.append(("warning",) + a)
app_module.messagebox.showerror = lambda *a, **k: dialogs.append(("error",) + a)
app_module.filedialog.asksaveasfilename = lambda **k: str(OUT / "export.csv")


def pump(root, n=20):
    for _ in range(n):
        root.update()


def wait_idle(root, gui, timeout=300):
    """Attend la fin du traitement en arrière-plan."""
    end = time.time() + timeout
    while gui.busy and time.time() < end:
        root.update()
        time.sleep(0.05)
    assert not gui.busy, "traitement trop long"
    pump(root)


def wait_dialog(dialog, timeout=120):
    end = time.time() + timeout
    while dialog.winfo_exists() and dialog.busy and time.time() < end:
        dialog.update()
        time.sleep(0.05)
    pump(dialog)


def shot_window(win, name):
    pump(win)
    x, y = win.winfo_rootx(), win.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + win.winfo_width(), y + win.winfo_height()), xdisplay=":99").save(OUT / name)
    print("capture :", OUT / name)


def shot(root, name):
    pump(root)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h), xdisplay=":99").save(OUT / name)
    print("capture :", OUT / name)


def main():
    root = tk.Tk()
    root.geometry("1500x900+0+0")
    gui = app_module.AnalyserDataApp(root)
    shot(root, "0_accueil.png")

    gui.open_files(EXAMPLES)
    wait_idle(root, gui)
    assert len(gui.session.measurements) == 2, dialogs
    shot(root, "1_import.png")

    # Afficher uniquement le shunt Graphtec + la température enceinte
    gui.select_all(False)
    shunt = ("GL980_Mes-_260601-170139.CSV", "Channel 4")
    gui._checked[shunt] = True
    gui._refresh_checkmarks()
    gui.redraw()
    shot(root, "2_shunt_brut.png")

    dialog = gui.clean_selected()
    wait_dialog(dialog)
    shot_window(dialog, "3a_fenetre_nettoyage.png")
    # Lisser ET forcer les repos à 0 (option cumulable, retour client v0.8.1)
    gui.chk_zero.set(True)
    dialog._changed()
    wait_dialog(dialog)
    bruit = dialog.result_labels[shunt][1].cget("text")
    assert "lissé" in bruit and "mis à 0" in bruit and not bruit.startswith("0 "), bruit
    shot_window(dialog, "3a_fenetre_nettoyage_zero.png")
    dialog.apply()
    wait_idle(root, gui)
    print("état :", gui.status.get())
    y = gui.session.measurements[shunt[0]].data[shunt[1]].to_numpy()
    assert (y == 0).sum() > 1000, "repos non mis à 0"
    shot(root, "3_shunt_nettoye.png")
    gui.chk_zero.set(False)

    # Livrables : image du graphique et rapport PDF
    app_module.filedialog.asksaveasfilename = lambda **k: str(OUT / "graphique.png")
    gui.export_image()
    wait_idle(root, gui)
    app_module.filedialog.asksaveasfilename = lambda **k: str(OUT / "rapport.pdf")
    gui.export_report()
    wait_idle(root, gui)
    app_module.filedialog.asksaveasfilename = lambda **k: str(OUT / "export.csv")
    assert (OUT / "graphique.png").exists() and (OUT / "rapport.pdf").exists(), dialogs
    journal = app_module.JournalWindow.open(root, gui.session.journal_text())
    shot_window(journal, "3b_journal.png")
    journal.destroy()
    print("livrables : graphique.png, rapport.pdf, journal OK")

    # Correction au clic : on abîme un point, puis on clique dessus sur le canevas
    df = gui.session.measurements[shunt[0]].data
    idx = 2000
    gui.session.set_value(shunt, idx, 8.0)
    gui.redraw()
    pump(root)
    line = gui.figure.axes[0].get_lines()[0]
    ax = line.axes
    ax.set_xlim(df["Time_min"].iloc[idx] - 0.2, df["Time_min"].iloc[idx] + 0.2)
    gui.canvas.draw()
    px, py = ax.transData.transform((df["Time_min"].iloc[idx], 8.0))
    widget = gui.canvas.get_tk_widget()
    height = widget.winfo_height()
    widget.event_generate("<Motion>", x=int(px), y=int(height - py))
    widget.event_generate("<ButtonPress-1>", x=int(px), y=int(height - py))
    widget.event_generate("<ButtonRelease-1>", x=int(px), y=int(height - py))
    pump(root)
    corrected = df[shunt[1]].iloc[idx]
    print("point corrigé :", corrected, "|", gui.status.get())
    assert corrected != 8.0, "la correction au clic n'a pas eu lieu"
    shot(root, "4_correction_clic.png")

    # Toutes les voies + décalage de la température de -4 min
    gui.select_all(True)
    gui._set_offset(-4.0)
    shot(root, "5_toutes_voies_decalage.png")

    gui.export_csv()
    wait_idle(root, gui)
    assert (OUT / "export.csv").exists(), dialogs
    print("export :", dialogs[-1])
    errors = [d for d in dialogs if d[0] == "error"]
    assert not errors, errors

    # Deux essais sans rapport (GL860 du 10/09 + nanodac du 01/06) : chacun part de 0
    gui.session.clear()
    gui._checked.clear()
    gui.open_files([str(ROOT / "exemples" / "autres_formats" / "GL860_1s.CSV"),
                    str(ROOT / "exemples" / "nanodac_Rd_Z.txt")])
    wait_idle(root, gui)
    warnings = [d for d in dialogs if d[0] == "warning"]
    assert warnings and "même période" in warnings[-1][2], warnings
    print("deux essais :", warnings[-1][2].splitlines()[-1][:120])
    shot(root, "8_deux_essais.png")

    # Même essai GL980 + nanodac : période commune, puis durée étirée
    gui.session.clear()
    gui._checked.clear()
    gui.open_files(EXAMPLES)
    wait_idle(root, gui)
    for label in ("Période commune", "Durée étirée (0-100 %)"):
        gui.time_mode_var.set(label)
        gui._on_time_mode()
        print(label, ":", gui.lbl_window.cget("text"))
        shot(root, "9_{}.png".format("commune" if "commune" in label else "etiree"))
    gui.time_mode_var.set("Heure réelle")
    gui._on_time_mode()

    # GL860 : petits courants permanents -> pas de suggestion, seuil vidé = rien d'effacé
    gui.session.clear()
    gui._checked.clear()
    gui.open_files([str(ROOT / "exemples" / "autres_formats" / "GL860_1s.CSV")])
    wait_idle(root, gui)
    dialog = gui.clean_selected()
    wait_dialog(dialog)
    shot_window(dialog, "10_nettoyage_gl860_defaut.png")
    dialog.use_suggestions()
    wait_dialog(dialog)
    shot_window(dialog, "11_nettoyage_gl860_suggestions.png")
    dialog.apply()
    wait_idle(root, gui)
    small = gui.session.measurements["GL860_1s.CSV"].data["Channel 11"]
    assert (small != 0).all(), "les petits courants ne doivent pas être effacés avec les suggestions"
    print("GL860 :", gui.status.get())
    # Bouton « Modifier les axes et les courbes » (barre d'outils)
    from analyser_data.axes_dialog import AxesDialog
    from analyser_data.plotting import format_hms
    axes_dialog = AxesDialog(gui, gui.selected_keys())
    pump(root)
    shot_window(axes_dialog, "15_axes.png")
    axes_dialog.x_vars[0].set("0:02:00")
    axes_dialog.x_vars[1].set("0:06:00")
    axes_dialog.title_var.set("Essai GL860 — cycle 1")
    first = gui.selected_keys()[0]
    name, color, width, line, marker, *_rest = axes_dialog.curve_vars[first]
    name.set("Alimentation")
    color.set("#d62728")
    width.set("2,5")
    line.set("Tirets")
    tabs = axes_dialog.nametowidget(axes_dialog.winfo_children()[0].winfo_children()[2])
    tabs.select(1)
    pump(root)
    shot_window(axes_dialog, "16_courbes.png")
    axes_dialog.ok()
    pump(root)
    view = gui.plot.current_view()
    assert (format_hms(view[0]), format_hms(view[1])) == ("0:02:00", "0:06:00"), view
    first_line = [l for l in gui.figure.axes[0].get_lines() if l.get_label() == "Alimentation"]
    assert first_line and first_line[0].get_linewidth() == 2.5 and first_line[0].get_linestyle() == "--"
    gui.redraw()  # changement de voies / nettoyage : réglages gardés
    assert format_hms(gui.plot.current_view()[0]) == "0:02:00"
    shot(root, "17_axes_appliques.png")
    gui.canvas.toolbar.home()  # Maison : limites automatiques, styles gardés
    assert gui.plot.current_view()[0] < 1
    assert any(l.get_label() == "Alimentation" for l in gui.figure.axes[0].get_lines())
    gui.plot_settings = app_module.PlotSettings()
    gui.redraw()
    assert gui.plot.legend is not None  # légende hors des courbes, à droite (issue #6)
    gui.canvas.toolbar.legend_button.invoke()
    pump(root)
    assert gui.plot.legend is None and gui.plot_settings.legend == "hidden"
    gui.canvas.toolbar.legend_button.invoke()
    pump(root)
    assert gui.plot.legend is not None and gui.plot_settings.legend == "right"
    print("axes et courbes : OK")

    # Import (issue #10) : classeur Excel à 2 feuilles -> aperçu, choix de la feuille, import
    import datetime as dt

    import openpyxl
    book = openpyxl.Workbook()
    notes = book.active
    notes.title = "Notes"
    notes.append(["Essai du 10/07/2026", "opérateur : X"])
    sheet = book.create_sheet("Mesures")
    sheet.append(["Date&Time", "U_supply (V)", "I_EOM (mA)", "Température (°C)"])
    for k in range(600):
        sheet.append([dt.datetime(2026, 7, 10, 9, 44, 13) + dt.timedelta(seconds=k), 24 + (k % 120 < 60),
                      150.0 if k % 120 < 60 else 0.0, 21.5 + k / 600])
    workbook = OUT / "essai_excel.xlsx"
    book.save(workbook)
    gui.session.clear()
    gui._checked.clear()
    gui._rebuild_tree()
    gui.open_files([str(workbook), str(EXAMPLES[0])], preview=True)
    dialog = next(w for w in root.winfo_children() if isinstance(w, ImportDialog))

    def wait_reads():
        end = time.time() + 60
        while len(dialog.results) < len(dialog.paths) and time.time() < end:
            root.update()
            time.sleep(0.05)
        pump(root)

    wait_reads()
    assert isinstance(dialog.results[str(workbook)], str)  # 1re feuille « Notes » : pas de mesures
    dialog.files.selection_set(str(workbook))
    dialog.sheet_var.set("Mesures")
    dialog._option_changed("sheet", "Mesures")
    wait_reads()
    shot_window(dialog, "18_import_excel.png")
    dialog.files.selection_set(str(EXAMPLES[0]))
    pump(root)
    shot_window(dialog, "19_import_apercu.png")
    dialog.accept()
    pump(root)
    assert len(gui.session.measurements) == 2, gui.session.measurements
    excel = next(m for m in gui.session.measurements.values() if m.sheet == "Mesures")
    assert [c.unit for c in excel.channels] == ["V", "mA", "°C"]
    print("import Excel : OK —", gui.status.get())
    shot(root, "20_excel_importe.png")

    # Correction de température (issue #11) : +0,5 °C, valeur hors plage refusée, mémorisée
    from analyser_data.i18n import get_setting
    gui.temp_offset_var.set("0,5")
    gui._on_temperature_offset()
    pump(root)
    temps = [l for a in gui.figure.axes for l in a.get_lines() if l.get_label().startswith("Température ambiante")]
    assert temps and temps[0].get_label() == "Température ambiante (+0,5 °C)", [l.get_label() for l in temps]
    assert get_setting("temperature_offset") == 0.5
    errors_before = len([d for d in dialogs if d[0] == "error"])
    gui.temp_offset_var.set("7")
    gui._on_temperature_offset()
    assert len([d for d in dialogs if d[0] == "error"]) == errors_before + 1
    assert gui.temp_offset_var.get() == "+0,5" and gui.session.temperature_offset == 0.5
    shot(root, "21_correction_temperature.png")
    gui.sidebar.canvas.yview_moveto(1.0)  # carte « Enceinte climatique », en bas de la colonne
    # Issue #13 : par défaut seules les voies °C suivent le curseur ; choix à la main d'une voie
    from analyser_data.corrections_dialog import ShiftedChannelsDialog
    gui._set_offset(-4.0)
    pump(root)
    shifted = [t for t in gui.plot._traces.values() if t.shiftable and not t.line.get_gid().startswith("raw")]
    assert all("Température" in t.line.get_label() for t in shifted) and shifted
    chooser = ShiftedChannelsDialog(gui)
    pump(root)
    excel_name = next(n for n, m in gui.session.measurements.items() if m.sheet)
    chooser.vars[(excel_name, "I_EOM (mA)")].set(True)
    shot_window(chooser, "24_voies_decalees.png")
    chooser.apply()
    pump(root)
    assert "I_EOM" in gui.lbl_shifted.cget("text")
    for t in gui.plot._traces.values():  # point tracé = temps enregistré - 4 min pour les voies choisies
        if t.shiftable:
            assert np.allclose(t.line.get_xdata() - t.x[t.index], -4.0)
    shot(root, "23_carte_enceinte.png")
    gui.apply_shifted_channels(None)
    gui._set_offset(0.0)
    # Par sonde : la sonde du classeur Excel réglée à part (-0,3 °C), prioritaire sur +0,5 °C
    from analyser_data.corrections_dialog import ProbeCorrectionsDialog
    probes = ProbeCorrectionsDialog(gui)
    pump(root)
    probe = next(iter(probes.vars))
    probes.vars[probe].set("-0,3")
    shot_window(probes, "22_corrections_par_sonde.png")
    probes.apply()
    pump(root)
    assert gui.session.value_offset(probe) == -0.3
    assert any("(-0,3 °C)" in l.get_label() for a in gui.figure.axes for l in a.get_lines())
    assert get_setting("channel_offsets") == {"{}::{}".format(*probe): -0.3}
    gui.apply_probe_corrections({probe: None})
    gui.temp_offset_var.set("0")
    gui._on_temperature_offset()
    assert gui.session.value_offset(probe) == 0.0
    print("correction de température : OK")

    help_window = app_module.HelpWindow.open(root, "Nettoyage")
    shot_window(help_window, "12_aide.png")
    help_window.destroy()

    # Langue : passage en anglais (fichiers et traitements conservés), puis retour en français
    gui.session.clear()
    gui._checked.clear()
    gui.open_files(EXAMPLES)
    wait_idle(root, gui)
    # Portable 1366×768 à 125 % (cas du client) : après le changement de langue, même vue,
    # et graduations / libellés entièrement visibles (pas d'effet « zoomé »).
    root.geometry("1366x728+0+0")
    pump(root)
    gui.plot.axes["main"].set_xlim(5, 25)
    gui.set_language("en")
    # Ce que fait Matplotlib sous Windows à 125 % quand le nouveau graphique s'affiche :
    # figure à 125 dpi et zone de dessin agrandie d'autant.
    root.tk.call("tk", "scaling", 1.25 * 96 / 72)
    gui.canvas._update_device_pixel_ratio()
    for _ in range(10):
        root.update()
        time.sleep(0.03)
    pump(root)
    widget = gui.canvas.get_tk_widget()
    right = widget.winfo_rootx() + widget.winfo_width()
    bottom = widget.winfo_rooty() + widget.winfo_height()
    assert right <= root.winfo_rootx() + root.winfo_width() and bottom <= root.winfo_rooty() + root.winfo_height(), \
        ("graphique plus grand que la fenêtre (effet zoom)", widget.winfo_width(), widget.winfo_height())
    assert "MultiPlotter for test benches" in root.title(), root.title()
    assert tuple(round(v, 6) for v in gui.plot.current_view()) == (5, 25), gui.plot.current_view()
    gui.canvas.draw()
    renderer = gui.figure.canvas.get_renderer()
    W, H = gui.figure.bbox.width, gui.figure.bbox.height
    for a in gui.figure.axes:
        box = a.get_tightbbox(renderer)
        assert box.x0 >= -2 and box.y0 >= -2 and box.x1 <= W + 2 and box.y1 <= H + 2, (box, W, H)
    print("langue : vue gardée, axes visibles à 125 %")
    root.tk.call("tk", "scaling", 96 / 72)
    gui.canvas._update_device_pixel_ratio()
    root.geometry("1500x900+0+0")
    pump(root)
    assert len(gui.session.measurements) == 2
    shot(root, "13_english.png")
    dialog = gui.clean_selected()
    wait_dialog(dialog)
    shot_window(dialog, "14_english_cleaning.png")
    dialog.destroy()
    gui.set_language("fr")
    pump(root)
    assert "bancs d'essai" in root.title()
    print("langues : FR -> EN -> FR OK")

    if len(sys.argv) > 2:  # gros fichier : indicateur de chargement + temps total
        gui.session.clear()
        gui._checked.clear()
        t0 = time.time()
        gui.open_files([sys.argv[2]])
        for _ in range(15):
            root.update()
            time.sleep(0.05)
        shot(root, "6_chargement_gros_fichier.png")
        wait_idle(root, gui)
        print("gros fichier chargé et affiché en %.1f s" % (time.time() - t0), "|", gui.status.get())
        shot(root, "7_gros_fichier.png")
        errors = [d for d in dialogs if d[0] == "error"]
        assert not errors, errors
    root.destroy()
    print("SMOKE OK")


if __name__ == "__main__":
    main()
