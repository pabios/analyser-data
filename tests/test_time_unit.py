"""Issue #18 : unité d'une colonne temps écrite en nombres sans unité, choisie à l'import.

Export CSV d'un enregistreur SEFRAM DAS1400 : le temps avance de 100 à chaque ligne (des ms,
un point toutes les 100 ms), mais l'unité n'est écrite nulle part. Par défaut l'appli suppose
des secondes : 5 618 lignes s'affichaient sur 156 h au lieu de 9 min 22 s.
"""
import numpy as np
import pytest

from analyser_data.loader import load_measurement
from analyser_data.session import Session

ROWS = 5618
HEADER = "Temps;U_supply (V);U_card (V);I_SMD (A);I_ICD (A)"


def sefram_csv(path, rows=ROWS, time=lambda i: str(i * 100)):
    lines = [HEADER] + ["{};14,1265;{};0,40222;-0,00503".format(time(i), "13,2759" if i % 6 in (2, 3, 4) else "-0,08675")
                        for i in range(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="cp1252")
    return path


def test_default_reads_unitless_numbers_as_seconds(tmp_path):
    """Comportement automatique inchangé (rien n'est corrigé sans le demander) : 100 = 100 s."""
    m = load_measurement(sefram_csv(tmp_path / "shock_B____0001.csv"))
    assert m.period_s == pytest.approx(100.0)
    assert m.duration_min * 60 == pytest.approx((ROWS - 1) * 100.0)
    assert m.time_unit == ""
    assert not any("Unité du temps" in w for w in m.warnings)


def test_time_unit_ms_gives_the_real_duration(tmp_path):
    m = load_measurement(sefram_csv(tmp_path / "shock_B____0001.csv"), time_unit="ms")
    assert m.period_s == pytest.approx(0.1)
    assert m.duration_min * 60 == pytest.approx((ROWS - 1) * 0.1)  # ≈ 9 min 22 s
    assert m.time_unit == "ms"
    assert any("Unité du temps imposée" in w and "millisecondes" in w for w in m.warnings)
    # les mesures ne changent pas
    assert [ch.label for ch in m.channels] == ["U_supply (V)", "U_card (V)", "I_SMD (A)", "I_ICD (A)"]
    assert m.data["U_supply (V)"].iloc[0] == pytest.approx(14.1265)


@pytest.mark.parametrize("unit, factor", [("s", 1.0), ("min", 60.0)])
def test_other_units(tmp_path, unit, factor):
    m = load_measurement(sefram_csv(tmp_path / "t.csv", rows=50, time=lambda i: str(i * 2)), time_unit=unit)
    assert m.period_s == pytest.approx(2 * factor)


def test_decimal_comma_times(tmp_path):
    m = load_measurement(sefram_csv(tmp_path / "t.csv", rows=50, time=lambda i: "{:.1f}".format(i * 0.5).replace(".", ",")),
                         time_unit="min")
    assert m.period_s == pytest.approx(30.0)


def test_sample_counter_with_unit_is_a_time(tmp_path):
    """Compteur 0, 1, 2… que l'utilisateur déclare en ms : pris tel quel, pas comme un compteur."""
    m = load_measurement(sefram_csv(tmp_path / "t.csv", rows=50, time=str), time_unit="ms")
    assert m.period_s == pytest.approx(0.001)
    assert not any("compteur" in w for w in m.warnings)


def test_unit_ignored_for_a_date_column(tmp_path):
    path = tmp_path / "dates.csv"
    lines = ["Date;U (V)"] + ["10/11/2026 10:18:{:02d};1,5".format(i) for i in range(30)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    m = load_measurement(path, time_unit="ms")
    assert m.period_s == pytest.approx(1.0)
    assert m.time_unit == ""
    assert any("ignorée" in w for w in m.warnings)


def test_unit_wins_over_inconsistent_header(tmp_path):
    """L'en-tête annonce 1 s (période × nombre de points) : le choix explicite de l'utilisateur fait foi."""
    path = tmp_path / "hdr.csv"
    lines = ["Sampling;1s", "Total data points;40", HEADER] + \
        ["{};14,1;0,1;0,4;0,0".format(i * 100) for i in range(40)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    m = load_measurement(path, time_unit="ms")
    assert m.period_s == pytest.approx(0.1)


def test_excel_workbook(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Temps", "U_supply (V)"])
    for i in range(40):
        ws.append([i * 100, 14.0 + i / 100])
    path = tmp_path / "sefram.xlsx"
    wb.save(path)
    m = load_measurement(path, time_unit="ms")
    assert m.period_s == pytest.approx(0.1)
    assert m.time_unit == "ms"


def test_session_options_and_journal(tmp_path):
    path = sefram_csv(tmp_path / "shock_B____0001.csv", rows=100)
    session = Session()
    loaded, errors = session.read_files([path], options={str(path): {"time_unit": "ms"}})
    assert not errors
    session.add_measurements(loaded)
    m = loaded[0]
    assert np.diff(m.elapsed_min * 60)[0] == pytest.approx(0.1)
    assert any("Unité du temps imposée à l'import pour shock_B____0001.csv : millisecondes" in text
               for _t, text in session.journal)
