"""Issue #13 : décaler dans le temps un fichier d'enceinte à plusieurs colonnes."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyser_data.loader import TIME_COL, load_measurement
from analyser_data.session import Session

EXAMPLES = Path(__file__).resolve().parent.parent / "exemples"


def _clim_file(tmp_path):
    """Enceinte climatique avec plusieurs colonnes : température, humidité, consigne,
    sortie de régulation en %, état de porte sans unité."""
    start = pd.Timestamp("2026-06-01 17:01:00")
    rows = ["Date/Heure\tTempérature (°C)\tHumidité (%HR)\tConsigne (°C)\tSortie régulation (%)\tPorte"]
    for k in range(45):
        t = start + pd.Timedelta(minutes=k)
        rows.append("{}\t{:.2f}\t{:.1f}\t25,0\t{:.1f}\t{}".format(
            t.strftime("%d/%m/%Y %H:%M:%S"), 20 + k / 10, 50 - k / 5, 40 + k, k % 2).replace(".", ","))
    path = tmp_path / "enceinte.txt"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_climatic_file_with_several_columns_follows_the_slider(tmp_path):
    clim = load_measurement(_clim_file(tmp_path))
    assert len(clim.channels) == 5 and clim.is_thermal  # consigne, % et porte ne l'empêchent plus
    gl980 = load_measurement(EXAMPLES / "GL980_Mes-_260601-170139.CSV")
    assert not gl980.is_thermal  # voies électriques : jamais décalé automatiquement

    s = Session()
    s.add_measurements([gl980, clim])
    keys = [(clim.name, c.label) for c in clim.channels] + [(gl980.name, "Channel 4")]
    shiftable = {p.key: p.shiftable for p in s.series(keys)}
    assert all(shiftable[k] for k in keys[:5]) and not shiftable[keys[5]]

    s.time_offset_min = 3.0
    out = tmp_path / "export.csv"
    s.export_csv(out, [keys[0], keys[5]])
    df = pd.read_csv(out, sep=";", decimal=",", encoding="utf-8-sig")
    column = [c for c in df.columns if "Température" in c][0]
    shifted_start = clim.data[TIME_COL].iloc[0] + 3.0  # 1re mesure de l'enceinte, décalée de 3 min
    row = df.iloc[(df[TIME_COL] - shifted_start).abs().idxmin()]
    assert row[column] == pytest.approx(clim.data[clim.channels[0].label].iloc[0])


def test_choose_the_shifted_file_by_hand(tmp_path):
    s = Session()
    s.load_files([str(EXAMPLES / "GL980_Mes-_260601-170139.CSV"), str(EXAMPLES / "nanodac_Rd_Z.txt")])
    gl, nano = list(s.measurements)
    assert [s.is_shifted(m) for m in s.measurements.values()] == [False, True]
    s.set_shifted_file(gl)  # choix manuel : seul ce fichier suit le curseur
    assert [s.is_shifted(m) for m in s.measurements.values()] == [True, False]
    assert "Décalage dans le temps appliqué à : " + gl in s.journal_text()
    s.set_shifted_file(None)
    assert [s.is_shifted(m) for m in s.measurements.values()] == [False, True]
