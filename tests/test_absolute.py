"""Retour client (#19) : partie d'un essai en négatif -> valeur absolue des données, sur la zone choisie."""
import numpy as np
import pandas as pd
import pytest

from analyser_data.loader import load_measurement
from analyser_data.session import Session


def make_file(path, n=600):
    """1 point par seconde ; la 2ᵉ moitié du courant est en négatif (shunt branché à l'envers)."""
    t0 = pd.Timestamp("2026-10-28 17:00:00")
    current = np.where(np.arange(n) < n // 2, 10.0, -10.0)
    rows = ["{};{:.1f};{:.1f}".format((t0 + pd.Timedelta(seconds=i)).strftime("%d/%m/%Y %H:%M:%S"),
                                      current[i], -2.0) for i in range(n)]
    path.write_text("\n".join(["Date;I (A);U (V)"] + rows) + "\n", encoding="utf-8")


@pytest.fixture
def session(tmp_path):
    make_file(tmp_path / "essai.csv")
    s = Session()
    s.add_measurements([load_measurement(tmp_path / "essai.csv")])
    return s


def test_only_the_zone_is_made_positive(session):
    key = next(k for k in session.all_keys() if "I" in k[1])
    changed = session.make_absolute([key], view=(7.0, 8.0))  # minutes 7 à 8 : 61 points négatifs
    assert changed[key] == 61
    y = session.measurements[key[0]].data[key[1]].to_numpy()
    assert (y[:300] == 10).all()  # avant : inchangé
    assert (y[420:481] == 10).all()  # zone : rendue positive
    assert (y[300:420] == -10).all() and (y[481:] == -10).all()  # hors zone : inchangé
    assert any("Valeur absolue |x| appliquée" in text and "61 point(s)" in text and "28/10/2026 17:07:00" in text
               for _t, text in session.journal)


def test_whole_test_without_zone_and_restore(session):
    keys = session.all_keys()
    changed = session.make_absolute(keys)
    assert sum(changed.values()) == 300 + 600
    for name, label in keys:
        assert (session.measurements[name].data[label].to_numpy() >= 0).all()
    session.restore_raw(keys)  # « Annuler le nettoyage »
    key = next(k for k in keys if "I" in k[1])
    assert (session.measurements[key[0]].data[key[1]].to_numpy()[300:] == -10).all()


def test_exports_use_the_absolute_values(session, tmp_path):
    key = next(k for k in session.all_keys() if "I" in k[1])
    session.make_absolute([key])
    df = session.export_csv(tmp_path / "export.csv", [key])
    column = [c for c in df.columns if "I" in c and c != "Time_min"][-1]
    assert (df[column].to_numpy(dtype=float) >= 0).all()
    assert "Valeur absolue" in (tmp_path / "export_journal.txt").read_text(encoding="utf-8")
