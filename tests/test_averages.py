"""Issue #16 : moyenne de deux voies (ou plus), affichée comme une voie de plus."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyser_data.cleaning import CleaningOptions
from analyser_data.loader import TIME_COL
from analyser_data.session import Session

EXAMPLES = Path(__file__).resolve().parent.parent / "exemples"
GL980 = EXAMPLES / "GL980_Mes-_260601-170139.CSV"
NANODAC = EXAMPLES / "nanodac_Rd_Z.txt"


def write_csv(path, t_s, columns, start="01/06/2026 10:00:00"):
    t0 = pd.Timestamp(pd.to_datetime(start, dayfirst=True))
    lines = ["Date;" + ";".join(columns)]
    for i, t in enumerate(t_s):
        stamp = (t0 + pd.Timedelta(seconds=float(t))).strftime("%d/%m/%Y %H:%M:%S")
        lines.append(stamp + ";" + ";".join("{:.4f}".format(v[i]).replace(".", ",") for v in columns.values()))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_average_of_two_channels_of_one_file():
    s = Session()
    s.load_files([str(GL980)])
    name = next(iter(s.measurements))
    m = s.measurements[name]
    a, b = m.data["Channel 4"].to_numpy().copy(), m.data["Channel 5"].to_numpy().copy()

    key = s.add_average([(name, "Channel 4"), (name, "Channel 5")])
    assert key == (name, "Moyenne Channel 4 / Channel 5 (A)")
    assert key in s.all_keys()  # une voie de plus : cochable, exportable
    series = s.series([key])[0]
    np.testing.assert_allclose(series.y, (a + b) / 2)
    assert series.label == "Moyenne Channel 4 / Channel 5 (A)" and series.unit == "A"
    # Mesures d'origine inchangées
    np.testing.assert_array_equal(m.data["Channel 4"].to_numpy(), a)
    np.testing.assert_array_equal(m.data["Channel 5"].to_numpy(), b)
    assert "Moyenne ajoutée" in s.journal_text()
    assert s.modified_points(key) == 0  # pas de « données brutes » en gris pour une moyenne


def test_average_follows_cleaning_and_point_corrections():
    s = Session()
    s.load_files([str(GL980)])
    name = next(iter(s.measurements))
    k4, k5 = (name, "Channel 4"), (name, "Channel 5")
    key = s.add_average([k4, k5])
    s.apply_cleaning([k4, key], CleaningOptions())  # la moyenne elle-même n'est pas nettoyée
    s.correct_point(k5, 100)
    m = s.measurements[name]
    expected = (m.data["Channel 4"].to_numpy() + m.data["Channel 5"].to_numpy()) / 2
    np.testing.assert_allclose(m.data[key[1]].to_numpy(), expected)
    with pytest.raises(ValueError, match="calculée"):
        s.correct_point(key, 10)
    s.restore_raw([k4, k5, key])
    np.testing.assert_allclose(m.data[key[1]].to_numpy(),
                               (s.raw_values(k4) + s.raw_values(k5)) / 2)


def test_different_units_are_refused():
    s = Session()
    s.load_files([str(GL980)])
    name = next(iter(s.measurements))
    with pytest.raises(ValueError, match="unités différentes"):
        s.add_average([(name, "Channel 3"), (name, "Channel 4")])  # V et A
    with pytest.raises(ValueError, match="au moins deux"):
        s.add_average([(name, "Channel 4")])
    assert not s.averages


def test_temperature_average_uses_corrected_values_once(tmp_path):
    t = np.arange(0, 600, 10.0)
    path = write_csv(tmp_path / "clim.csv", t, {"T1 (°C)": 20 + 0 * t, "T2 (°C)": 22 + 0 * t})
    s = Session()
    s.load_files([str(path)])
    name = next(iter(s.measurements))
    key = s.add_average([(name, "T1 (°C)"), (name, "T2 (°C)")])
    assert key[1] == "Moyenne T1 / T2 (°C)"
    np.testing.assert_allclose(s.displayed_values(key), 21.0)
    s.set_temperature_offset(0.5)  # chaque sonde +0,5 -> moyenne +0,5 (pas +1,0)
    np.testing.assert_allclose(s.displayed_values(key), 21.5)
    s.set_channel_offset((name, "T1 (°C)"), -1.0)
    np.testing.assert_allclose(s.displayed_values(key), (19.0 + 22.5) / 2)
    labels = [p.label for p in s.series([(name, "T1 (°C)"), (name, "T2 (°C)"), key])]
    assert labels[2] == "Moyenne T1 / T2 (°C)"
    assert labels[0].startswith("Température ambiante 1") and labels[1].startswith("Température ambiante 2")


def test_average_across_files_interpolates_without_bridging_gaps(tmp_path):
    t1 = np.arange(0, 3600, 1.0)  # 1 s
    t2 = np.r_[np.arange(0, 1200, 10.0), np.arange(2400, 3600, 10.0)]  # 10 s, coupure de 20 min
    a = write_csv(tmp_path / "a.csv", t1, {"T (°C)": 20 + t1 / 3600})
    b = write_csv(tmp_path / "b.csv", t2, {"T (°C)": 30 + t2 / 3600})
    s = Session()
    s.load_files([str(a), str(b)])
    na, nb = list(s.measurements)
    key = s.add_average([(na, "T (°C)"), (nb, "T (°C)")])
    assert key[0] == na and "[{}]".format(nb) in key[1]
    x = s.measurements[na].data[TIME_COL].to_numpy()
    y = s.displayed_values(key)
    inside = (x * 60 < 1190) | ((x * 60 > 2400) & (x * 60 < 3590))
    np.testing.assert_allclose(y[inside], 25 + (x[inside] * 60) / 3600, atol=1e-3)
    assert np.isnan(y[(x * 60 > 1200) & (x * 60 < 2400)]).all()  # pas de moyenne inventée dans la coupure

    s.remove_file(nb)  # une voie déchargée -> la moyenne disparaît
    assert not s.averages and key not in s.all_keys()


def test_average_is_exported_and_removable(tmp_path):
    s = Session()
    s.load_files([str(GL980)])
    name = next(iter(s.measurements))
    key = s.add_average([(name, "Channel 4"), (name, "Channel 5")])
    out = tmp_path / "export.csv"
    df = s.export_csv(out, [(name, "Channel 4"), key])
    assert any("Moyenne Channel 4 / Channel 5" in c for c in df.columns)
    s.remove_average(key)
    assert key not in s.all_keys() and key[1] not in s.measurements[name].data.columns
    assert "Moyenne retirée" in s.journal_text()


def test_average_survives_time_mode_and_cycle_undo():
    s = Session()
    s.load_files([str(GL980), str(NANODAC)])
    gl = next(iter(s.measurements))
    key = s.add_average([(gl, "Channel 4"), (gl, "Channel 5")])
    for mode in ("zero", "stretch", "common", "real"):
        s.set_time_mode(mode)
        assert key[1] in s.measurements[gl].data.columns
    s.clear()
    assert not s.averages
