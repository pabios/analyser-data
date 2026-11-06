"""Issue #16 : fichiers d'enceinte climatique (°C + %HR) sous Excel, exemples fournis au client,
fichier .GBD de test, et moyenne de voies avec le décalage « Enceinte climatique »."""
import builtins
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyser_data.channels import HUMIDITY, TEMPERATURE
from analyser_data.loader import TIME_COL, load_measurement
from analyser_data.session import Session, is_clim_file, legend_label

EXAMPLES = Path(__file__).resolve().parent.parent / "exemples"
CLIM = EXAMPLES / "enceinte"
START = dt.datetime(2026, 11, 2, 23, 0, 0)  # passe minuit : la date doit suivre l'heure


def _xlsx(path, rows, formats=None):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    for r, row in enumerate(rows, start=1):
        sheet.append(row)
        for c, fmt in (formats or {}).items():
            if r > 1:
                sheet.cell(row=r, column=c).number_format = fmt
    book.save(path)
    return path


def _no_openpyxl(monkeypatch):
    real_import = builtins.__import__

    def fake(name, *args, **kwargs):
        if name == "openpyxl" or name.startswith("openpyxl."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)


def _date_hour_rows(n=180):
    rows = [["Date", "Heure", "Température [°C]", "Humidité [%HR]"]]
    for k in range(n):
        moment = START + dt.timedelta(minutes=k)
        rows.append([moment.replace(hour=0, minute=0, second=0), moment.time(), 20.0 + k / 10, 50.0 + k / 20])
    return rows


@pytest.mark.parametrize("builtin_reader", [False, True])
def test_excel_date_and_hour_in_two_columns(tmp_path, monkeypatch, builtin_reader):
    """Bug « l'appli beug » avec les fichiers clim Excel : la cellule « Date » est une vraie date Excel
    (lue « 2026-11-02 00:00:00 ») ; l'heure était ignorée -> une mesure par jour (« période 1440 min »)."""
    path = _xlsx(tmp_path / "enceinte.xlsx", _date_hour_rows(), {1: "dd/mm/yyyy", 2: "hh:mm:ss"})
    if builtin_reader:
        _no_openpyxl(monkeypatch)
    m = load_measurement(path)
    assert len(m.data) == 180 and m.period_s == pytest.approx(60)
    np.testing.assert_allclose(m.data[TIME_COL].to_numpy(), np.arange(180), atol=1e-6)  # minuit franchi
    assert m.start == pd.Timestamp(START)
    assert [c.label for c in m.channels] == ["Température (°C)", "Humidité (%HR)"]
    assert [c.quantity for c in m.channels] == [TEMPERATURE, HUMIDITY]
    assert not any("Heure" == c.label for c in m.channels)


def test_excel_hour_column_holding_the_full_date(tmp_path):
    """Colonne « Heure » qui contient la date complète (affichée hh:mm) : elle suffit."""
    rows = [["Date", "Heure", "T [°C]"]] + [
        [(START + dt.timedelta(minutes=k)).date(), START + dt.timedelta(minutes=k), 20.0 + k] for k in range(120)]
    m = load_measurement(_xlsx(tmp_path / "enceinte.xlsx", rows, {2: "hh:mm"}))
    assert len(m.data) == 120 and m.period_s == pytest.approx(60)
    assert m.start == pd.Timestamp(START)


def test_excel_nanodac_descriptor_split_over_several_cells(tmp_path):
    """Fichier nanodac ouvert dans Excel en coupant aussi au « ; » : le descriptif de chaque voie
    occupe 4 cellules. Les noms ne doivent pas se décaler (« Group 1 » n'est pas une voie)."""
    rows = [["Date/Heure", "Channel 2       (ENAN2)", "Group 1", "M402-M210", "°C",
             "Channel 3       (ENAN3)", "Group 1", "M403-H110", "%HR"]]
    rows += [[START + dt.timedelta(minutes=k), 25.0 + k / 100, 60.0 - k / 100] for k in range(100)]
    m = load_measurement(_xlsx(tmp_path / "nanodac.xlsx", rows))
    assert [c.label for c in m.channels] == ["Channel 2", "Channel 3"]
    assert [c.unit for c in m.channels] == ["°C", "%HR"]
    assert [legend_label(c, real_name=is_clim_file(m)) for c in m.channels] == [
        "Channel 2 – M402-M210 (°C)", "Channel 3 – M403-H110 (%HR)"]
    np.testing.assert_allclose(m.data["Channel 3"].to_numpy(), 60.0 - np.arange(100) / 100)


@pytest.mark.parametrize("name", ["nanodac_T_HR.txt", "nanodac_T_HR.xlsx", "enceinte_T_HR.xlsx"])
def test_climatic_examples_given_to_the_client(name):
    m = load_measurement(CLIM / name)
    assert is_clim_file(m)
    assert len(m.data) == 1440 and m.period_s == pytest.approx(60)
    assert m.start == pd.Timestamp("2026-11-02 08:00:00")
    assert [c.quantity for c in m.channels] == [TEMPERATURE, TEMPERATURE, HUMIDITY, HUMIDITY]
    temperature = m.data[m.channels[1].label].to_numpy()
    assert 24 < temperature.min() and 54 < temperature.max() < 56
    if name.startswith("nanodac"):
        assert [legend_label(c, real_name=True) for c in m.channels] == [
            "Channel 1 – M401-T120 (°C)", "Channel 2 – M402-T121 (°C)",
            "Channel 3 – M403-H110 (%HR)", "Channel 4 – M404-H111 (%HR)"]


def test_example_gbd_matches_its_csv_export():
    gbd = load_measurement(EXAMPLES / "autres_formats" / "GL860_test.GBD")
    csv = load_measurement(EXAMPLES / "autres_formats" / "GL860_test_export.CSV")
    assert [c.label for c in gbd.channels] == [c.label for c in csv.channels]
    assert [c.unit for c in gbd.channels] == [c.unit for c in csv.channels] == ["V", "mV", "°C"]
    assert gbd.start == csv.start and len(gbd.data) == len(csv.data) == 6000
    for c in gbd.channels:
        np.testing.assert_allclose(gbd.data[c.label].to_numpy(), csv.data[c.label].to_numpy(), atol=1e-9)
    np.testing.assert_allclose(gbd.data[TIME_COL].to_numpy(), csv.data[TIME_COL].to_numpy(), atol=1e-9)


def test_averages_of_the_climatic_example():
    s = Session()
    s.load_files([str(CLIM / "nanodac_T_HR.txt")])
    name = next(iter(s.measurements))
    m = s.measurements[name]
    t1, t2, h1, h2 = [m.data[c.label].to_numpy().copy() for c in m.channels]
    kt = s.add_average([(name, m.channels[0].label), (name, m.channels[1].label)])
    kh = s.add_average([(name, m.channels[2].label), (name, m.channels[3].label)])
    np.testing.assert_allclose(s.displayed_values(kt), (t1 + t2) / 2)
    np.testing.assert_allclose(s.displayed_values(kh), (h1 + h2) / 2)
    assert s.measurements[name].channel(kh[1]).quantity == HUMIDITY
    # le décalage « Enceinte climatique » : la moyenne °C suit ses voies, la moyenne %HR reste
    assert s.set_time_offset(5.0) is False  # rien à recalculer
    shifted = {k: x.shiftable for k, x in zip([kt, kh], s.series([kt, kh]))}
    assert shifted == {kt: True, kh: False}


def _ramp_files(tmp_path):
    from test_averages import write_csv

    t = np.arange(0, 7200, 60.0)
    clim = write_csv(tmp_path / "clim.csv", t, {"T1 (°C)": 20 + t / 60, "T2 (°C)": 30 + t / 60})
    el = write_csv(tmp_path / "elec.csv", np.arange(0, 7200, 10.0), {"Tc (°C)": 20 + np.arange(0, 7200, 10.0) / 60})
    s = Session()
    s.load_files([str(clim), str(el)])
    return s, list(s.measurements)


def _displayed_at(s, key, minute):
    series = s.series([key])[0]
    shift = s.time_offset_min if series.shiftable else 0.0
    return float(np.interp(minute, series.x + shift, series.y))


def test_average_of_a_shifted_and_an_unshifted_channel(tmp_path):
    """La moyenne est celle des courbes AFFICHÉES, même si une seule de ses voies suit le curseur."""
    s, (nc, ne) = _ramp_files(tmp_path)
    s.set_shifted_keys([(nc, "T1 (°C)")])  # seule T1 suit le curseur
    key = s.add_average([(nc, "T1 (°C)"), (ne, "Tc (°C)")])
    assert s.mixed_shift_averages() == [key] and not s.is_key_shifted(key)
    assert s.set_time_offset(10.0) is True  # recalculée
    for minute in (30.0, 60.0, 100.0):
        expected = (_displayed_at(s, (nc, "T1 (°C)"), minute) + _displayed_at(s, (ne, "Tc (°C)"), minute)) / 2
        assert _displayed_at(s, key, minute) == pytest.approx(expected, abs=1e-6)
    assert np.isnan(s.displayed_values(key)[0])  # T1 décalée de 10 min n'a pas encore de valeur à 0
    s.set_time_offset(0.0)
    np.testing.assert_allclose(s.displayed_values(key)[:20], 20 + np.arange(20), atol=1e-9)  # une valeur par minute


def test_average_follows_the_shift_of_its_channels(tmp_path):
    s, (nc, _ne) = _ramp_files(tmp_path)
    key = s.add_average([(nc, "T1 (°C)"), (nc, "T2 (°C)")])
    assert s.is_key_shifted(key)  # ses deux voies °C suivent le curseur
    s.set_shifted_keys([])
    assert not s.is_key_shifted(key)
    s.set_shifted_keys([(nc, "T1 (°C)"), (nc, "T2 (°C)")])
    assert s.is_key_shifted(key) and key in s.shifted_channels()
    s.set_time_offset(7.5)
    for minute in (20.0, 90.0):
        expected = (_displayed_at(s, (nc, "T1 (°C)"), minute) + _displayed_at(s, (nc, "T2 (°C)"), minute)) / 2
        assert _displayed_at(s, key, minute) == pytest.approx(expected, abs=1e-6)
