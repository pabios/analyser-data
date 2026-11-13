"""Issue #18 : voies de pression (mbar, bar, Pa, hPa, kPa, MPa, psi…) reconnues.

Avant : seules V, A, °C et %HR étaient des unités connues. Dans un fichier nanodac, l'unité
« mbar » du descriptif de voie était ignorée : la voie de pression s'appelait « Channel 5 »,
sans unité ni repère de sonde, et l'axe du graphique n'avait pas de titre.
"""
from pathlib import Path

import numpy as np
import pytest
from matplotlib.figure import Figure

from analyser_data.channels import OTHER, PRESSURE, TEMPERATURE, make_channel, normalize_unit, quantity_of
from analyser_data.loader import load_measurement
from analyser_data.plotting import PlotManager
from analyser_data.session import Session, is_clim_file, legend_label

EXAMPLE = Path(__file__).resolve().parent.parent / "exemples" / "enceinte" / "nanodac_T_P.txt"


@pytest.mark.parametrize("written, unit", [
    ("mbar", "mbar"), ("MBAR", "mbar"), ("bar", "bar"), ("barg", "bar"), ("Pa", "Pa"), ("hPa", "hPa"),
    ("HPA", "hPa"), ("kPa", "kPa"), ("Kpa", "kPa"), ("MPa", "MPa"), ("psi", "psi"), ("PSIA", "psi"),
    ("mmHg", "mmHg"), ("Torr", "Torr"),
])
def test_pressure_units(written, unit):
    assert normalize_unit(written) == unit
    assert quantity_of(unit) == PRESSURE


def test_pressure_from_name_without_unit():
    assert make_channel("Pression").quantity == PRESSURE
    assert make_channel("Pressure_chamber").quantity == PRESSURE
    assert make_channel("Druck").quantity == PRESSURE
    assert make_channel("Pression (mbar)").label == "Pression (mbar)"
    assert make_channel("Débit (l/min)").quantity == OTHER  # unités inconnues : inchangé


def write_nanodac(path, unit):
    head = ("Date/Heure\tChannel 1       (ENAN1);Group 1;M401-T120;°C\t"
            "Channel 5       (ENAN5);Group 1;M405-P130;{}".format(unit))
    rows = ["02/11/26 08:{:02d}:00\t25,1\t1013,2".format(i) for i in range(30)]
    path.write_text("\n".join([head] + rows) + "\n", encoding="cp1252")
    return path


@pytest.mark.parametrize("unit", ["mbar", "bar", "hPa", "kPa", "Pa", "psi"])
def test_nanodac_pressure_channel_keeps_unit_and_probe(tmp_path, unit):
    m = load_measurement(write_nanodac(tmp_path / "essai.txt", unit))
    temp, press = m.channels
    assert (temp.unit, temp.quantity) == ("°C", TEMPERATURE)
    assert (press.label, press.unit, press.quantity, press.alias) == ("Channel 5", unit, PRESSURE, "M405-P130")
    assert legend_label(press, real_name=is_clim_file(m)) == "Channel 5 – M405-P130 ({})".format(unit)
    assert m.data["Channel 5"].iloc[0] == pytest.approx(1013.2)  # valeurs inchangées


def test_split_nanodac_descriptor_with_pressure_unit():
    """En-tête éclaté sur plusieurs cellules (classeur Excel, issue #16) : la voie qui finit par
    « mbar » est regroupée comme celles en °C (sinon les noms des voies se décalent)."""
    from analyser_data.loader import _regroup_descriptors

    cells = ["Date/Heure", "Channel 1 (ENAN1)", "Group 1", "M401-T120", "°C",
             "Channel 5 (ENAN5)", "Group 1", "M405-P130", "mbar"]
    assert _regroup_descriptors(cells, 3) == [
        "Date/Heure", "Channel 1 (ENAN1);Group 1;M401-T120;°C", "Channel 5 (ENAN5);Group 1;M405-P130;mbar"]


def test_example_file_and_plot_axes():
    s = Session()
    s.load_files([str(EXAMPLE)])
    name = next(iter(s.measurements))
    m = s.measurements[name]
    assert [(ch.unit, ch.quantity) for ch in m.channels] == [("°C", TEMPERATURE), ("mbar", PRESSURE)]
    assert m.period_s == pytest.approx(10.0) and len(m.data) == 720
    assert m.data["Channel 5"].min() < 320 and m.data["Channel 5"].max() > 1000

    plot = PlotManager(Figure())
    plot.draw(s.series([(name, "Channel 1"), (name, "Channel 5")]))
    labels = {k: a.get_ylabel() for k, a in plot.axes.items()}
    assert labels["main"] == "Pression (mbar)"  # pression à gauche, titrée
    assert "Température (°C)" in labels.values()  # température sur son axe à droite
    pressure_line = next(l for l in plot.axes["main"].get_lines() if "M405-P130" in l.get_label())
    assert np.nanmax(pressure_line.get_ydata()) > 1000
