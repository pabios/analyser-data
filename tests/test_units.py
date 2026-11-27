"""Issue #20 : conversion d'unités d'une voie (A <-> mA, V <-> mV, mbar <-> bar…)."""
import numpy as np
import pandas as pd
import pytest

from analyser_data.channels import CURRENT, compatible_units, conversion_factor, normalize_unit, quantity_of
from analyser_data.cleaning import CleaningOptions
from analyser_data.loader import load_measurement
from analyser_data.session import Session


def make_file(path, n=200):
    t0 = pd.Timestamp("2026-11-27 10:00:00")
    rows = ["{};{:.3f};{:.3f};{:.2f};{:.1f}".format(
        (t0 + pd.Timedelta(seconds=i)).strftime("%d/%m/%Y %H:%M:%S"), 2.5 + (i % 10) / 100, 2.4, 12.0, 1013.0 + i / 10)
        for i in range(n)]
    path.write_text("\n".join(["Date;I (A);I2 (A);U (V);P (mbar)"] + rows) + "\n", encoding="utf-8")


@pytest.fixture
def session(tmp_path):
    make_file(tmp_path / "essai.csv")
    s = Session()
    s.add_measurements([load_measurement(tmp_path / "essai.csv")])
    return s


def key_of(session, unit):
    return next((m.name, c.label) for m in session.measurements.values() for c in m.channels if c.unit == unit)


def values(session, key):
    return session.measurements[key[0]].data[key[1]].to_numpy(dtype=float)


def test_conversion_table():
    assert conversion_factor("A", "mA") == pytest.approx(1000)
    assert conversion_factor("mA", "A") == pytest.approx(0.001)
    assert conversion_factor("V", "mV") == pytest.approx(1000)
    assert conversion_factor("mbar", "bar") == pytest.approx(0.001)
    assert conversion_factor("bar", "kPa") == pytest.approx(100)
    assert conversion_factor("psi", "kPa") == pytest.approx(6.894757, rel=1e-6)
    assert compatible_units("A") == ["µA", "mA", "A", "kA"]
    assert compatible_units("°C") == [] and compatible_units("") == []
    with pytest.raises(ValueError):
        conversion_factor("A", "V")
    assert normalize_unit("kA") == "kA" and quantity_of("kA") == CURRENT


def test_a_to_ma_and_back(session, tmp_path):
    key = key_of(session, "A")
    assert key[1] == "I (A)"
    before = values(session, key).copy()
    new = session.convert_unit(key, "mA")
    assert new == (key[0], "I (mA)")  # le nom portait l'unité : il la suit
    assert session.measurements[key[0]].channel(new[1]).unit == "mA"
    np.testing.assert_allclose(values(session, new), before * 1000)
    np.testing.assert_allclose(session.raw_values(new), before * 1000)  # brutes aussi : restauration cohérente
    assert session.modified_points(new) == 0
    assert any("Unité convertie" in t and "A -> mA" in t and "I (A) -> I (mA)" in t for _t, t in session.journal)
    df = session.export_csv(tmp_path / "export.csv", [new])
    assert "I (mA)" in df.columns and "I (A)" not in df.columns
    assert session.convert_unit(new, "A") == key
    np.testing.assert_allclose(values(session, key), before, rtol=1e-12)


def test_cleaning_settings_and_restore_follow(session):
    key = key_of(session, "A")
    raw = values(session, key).copy()
    session.noise_thresholds[key] = 0.05
    session.apply_cleaning([key], CleaningOptions())
    new = session.convert_unit(key, "mA")
    assert session.noise_thresholds[new] == pytest.approx(50) and key not in session.noise_thresholds
    session.restore_raw([new])  # « Annuler le nettoyage » : brutes, mais en mA
    np.testing.assert_allclose(values(session, new), raw * 1000)


def test_pressure_mbar_to_bar(session):
    new = session.convert_unit(key_of(session, "mbar"), "bar")
    assert new[1] == "P (bar)"
    assert values(session, new)[0] == pytest.approx(1.013)
    assert session.measurements[new[0]].channel(new[1]).quantity == "pression"


def test_graphtec_channel_name_unchanged(tmp_path):
    from test_merge import graphtec
    graphtec(tmp_path / "g.CSV", "2026-11-27 10:00:00", 30)  # CH1 V, CH2 A
    s = Session()
    s.add_measurements([load_measurement(tmp_path / "g.CSV")])
    key = key_of(s, "A")
    assert s.convert_unit(key, "mA") == key  # « Channel 2 » : l'unité est à part
    assert s.measurements[key[0]].channel(key[1]).unit == "mA"


def test_refused_cases(session):
    key = key_of(session, "A")
    with pytest.raises(ValueError, match="convertir"):
        session.convert_unit(key, "V")
    other = next(k for k in session.all_keys() if k[1] == "I2 (A)")
    average = session.add_average([key, other])
    with pytest.raises(ValueError, match="retirez d'abord"):
        session.convert_unit(key, "mA")
    with pytest.raises(ValueError, match="moyenne"):
        session.convert_unit(average, "mA")
    assert session.measurements[key[0]].channel(key[1]).unit == "A"  # rien n'a changé
