"""Issue #16 : import « universel » — séparateur « | », JSON, Parquet, formats SIG refusés clairement."""
import json

import numpy as np
import pandas as pd
import pytest

from analyser_data.loader import TIME_COL, LoadError, load_measurement


def _frame(n=300):
    t = pd.Timestamp("2026-07-10 09:00:00") + pd.to_timedelta(np.arange(n), unit="s")
    return pd.DataFrame({"Date/Heure": t, "Tension (V)": 24 + np.sin(np.arange(n) / 20),
                         "Courant (A)": np.round(np.cos(np.arange(n) / 30), 3)})


def _check(m, n=300):
    assert [c.unit for c in m.channels] == ["V", "A"]
    assert len(m.data) == n and m.duration_min == pytest.approx((n - 1) / 60)
    np.testing.assert_allclose(m.data["Courant (A)"].to_numpy()[:3], np.round(np.cos(np.arange(3) / 30), 3))


@pytest.mark.parametrize("decimal", [".", ","])
def test_pipe_separated(tmp_path, decimal):
    df = _frame()
    lines = ["|".join(df.columns)] + ["{}|{}|{}".format(r[0].strftime("%d/%m/%Y %H:%M:%S"),
                                                         repr(r[1]).replace(".", decimal), repr(r[2]).replace(".", decimal))
                                       for r in df.itertuples(index=False)]
    path = tmp_path / "mesures.txt"
    path.write_text("\n".join(lines) + "\n", encoding="cp1252")
    _check(load_measurement(path))


@pytest.mark.parametrize("orient", ["records", "columns", "lines"])
def test_json(tmp_path, orient):
    df = _frame()
    path = tmp_path / "mesures.json"
    if orient == "lines":
        df.to_json(path, orient="records", lines=True, date_format="iso")
    else:
        df.to_json(path, orient=orient, date_format="iso")
    _check(load_measurement(path))


def test_parquet(tmp_path):
    pytest.importorskip("pyarrow")
    path = tmp_path / "mesures.parquet"
    _frame().to_parquet(path)
    m = load_measurement(path)
    _check(m)
    assert m.start == pd.Timestamp("2026-07-10 09:00:00")


@pytest.mark.parametrize("name", ["reseau.gdb", "parcelles.shp", "zones.geojson"])
def test_gis_files_are_refused_with_a_clear_message(tmp_path, name):
    path = tmp_path / name
    path.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    with pytest.raises(LoadError, match="données géographiques.*GBD"):
        load_measurement(path)
