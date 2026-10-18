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


def test_only_temperature_channels_follow_the_slider_by_default(tmp_path):
    """Réponse du client : « je veux décaler juste les channels en °C » (de n'importe quel fichier)."""
    clim = load_measurement(_clim_file(tmp_path))
    assert len(clim.channels) == 5
    gl980 = load_measurement(EXAMPLES / "GL980_Mes-_260601-170139.CSV")

    s = Session()
    s.add_measurements([gl980, clim])
    keys = [(clim.name, c.label) for c in clim.channels] + [(gl980.name, "Channel 4")]
    shiftable = {p.key: p.shiftable for p in s.series(keys)}
    units = {(clim.name, c.label): c.unit for c in clim.channels}
    for key in keys[:5]:
        assert shiftable[key] == (units[key] == "°C"), key  # %HR, %, porte : à leur heure
    assert not shiftable[keys[5]]

    s.time_offset_min = 3.0
    out = tmp_path / "export.csv"
    temp_key, hum_key = keys[0], keys[1]
    s.export_csv(out, [temp_key, hum_key, keys[5]])
    df = pd.read_csv(out, sep=";", decimal=",", encoding="utf-8-sig")
    columns = [c for c in df.columns if c not in (TIME_COL, "Temps (H:MM:SS)")]
    assert "Température" in columns[0] and "Humidité" in columns[1]  # ordre des voies conservé
    start = clim.data[TIME_COL].iloc[0]
    row = df.iloc[(df[TIME_COL] - (start + 3.0)).abs().idxmin()]
    assert row[columns[0]] == pytest.approx(clim.data[temp_key[1]].iloc[0])  # °C : décalée de 3 min
    row = df.iloc[(df[TIME_COL] - start).abs().idxmin()] if start >= 0 else df.iloc[0]
    hum_at = clim.data[hum_key[1]].iloc[int(round(df[TIME_COL].iloc[row.name] - start))]
    assert row[columns[1]] == pytest.approx(hum_at)  # %HR : pas décalée


def test_choose_the_shifted_channels_by_hand():
    """« je choisis moi-même » : les voies cochées suivent le curseur, et elles seules."""
    s = Session()
    s.load_files([str(EXAMPLES / "GL980_Mes-_260601-170139.CSV"), str(EXAMPLES / "nanodac_Rd_Z.txt")])
    gl, nano = list(s.measurements)
    assert s.shifted_channels() == [(nano, "Channel 2")]  # défaut : la voie °C
    s.set_shifted_keys({(gl, "Channel 3"), (nano, "Channel 2")})
    assert s.shifted_channels() == [(gl, "Channel 3"), (nano, "Channel 2")]
    assert not s.is_key_shifted((gl, "Channel 4"))
    assert "Décalage dans le temps appliqué à : Channel 3 [" in s.journal_text()
    s.set_shifted_keys(set())
    assert s.shifted_channels() == []
    s.set_shifted_keys(None)
    assert s.shifted_channels() == [(nano, "Channel 2")]
