"""Issue #11 : correction de température (affichée = mesurée + correction, -5,0 à +5,0 °C, pas 0,1)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyser_data.session import Session, parse_temperature_offset

EXAMPLES = Path(__file__).resolve().parent.parent / "exemples"


@pytest.mark.parametrize("text, value", [("0", 0.0), ("0,5", 0.5), ("-1.2", -1.2), ("+5", 5.0), ("-5,0", -5.0),
                                         ("0,04", 0.0), ("0,05", 0.1), ("1,26", 1.3), ("", 0.0), ("2 °C", 2.0),
                                         ("-0,0", 0.0)])
def test_parse_offset(text, value):
    result = parse_temperature_offset(text)
    assert result == value and str(result) == str(value)  # pas de 0,30000000000000004


@pytest.mark.parametrize("text", ["5,1", "-6", "10", "abc", "nan", "1,2,3"])
def test_out_of_range_is_refused(text):
    with pytest.raises(ValueError, match="entre -5,0 et \\+5,0 °C"):
        parse_temperature_offset(text)


def test_offset_applies_to_temperature_only_and_keeps_raw_data(tmp_path):
    s = Session()
    s.load_files([str(EXAMPLES / "GL980_Mes-_260601-170139.CSV"), str(EXAMPLES / "nanodac_Rd_Z.txt")])
    gl, nano = list(s.measurements)
    temp, current = (nano, "Channel 2"), (gl, "Channel 4")
    measured = s.measurements[nano].data["Channel 2"].to_numpy().copy()
    current_values = s.measurements[gl].data["Channel 4"].to_numpy().copy()

    s.set_temperature_offset(0.1 + 0.2)
    assert s.temperature_offset == 0.3
    series = {p.key: p for p in s.series([temp, current])}
    np.testing.assert_allclose(series[temp].y, measured + 0.3)
    np.testing.assert_array_equal(series[current].y, current_values)  # le courant n'est pas touché
    assert series[temp].label == "Température ambiante (+0,3 °C)"
    np.testing.assert_array_equal(s.measurements[nano].data["Channel 2"].to_numpy(), measured)  # mesure intacte

    out = tmp_path / "export.csv"
    s.export_csv(out, [current, temp])
    exported = pd.read_csv(out, sep=";", decimal=",", encoding="utf-8-sig")
    column = [c for c in exported.columns if "Channel 2" in c][0]
    values = exported[column].dropna().unique()
    assert len(values) and np.isin(np.round(values - 0.3, 6), np.round(measured, 6)).all()  # mesure + 0,3
    assert "correction de température +0,3 °C" in s.journal_text()

    s.set_temperature_offset(0)  # valeur nulle : affichage = mesure
    np.testing.assert_array_equal(s.series([temp])[0].y, measured)
    assert s.series([temp])[0].label == "Température ambiante"


def test_report_statistics_include_the_correction(tmp_path):
    from analyser_data.report import build_report

    s = Session()
    s.load_files([str(EXAMPLES / "nanodac_Rd_Z.txt")])
    s.set_temperature_offset(-2.0)
    key = (next(iter(s.measurements)), "Channel 2")
    assert s.displayed_values(key).max() == pytest.approx(s.measurements[key[0]].data[key[1]].max() - 2.0)
    build_report(s, [key], None, tmp_path / "rapport.pdf")
    assert "Correction de température : -2,0 °C" in s.journal_text()
