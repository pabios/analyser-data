"""Issue #15 : l'axe X doit montrer la durée réelle de l'essai (3 h -> 3 h, pas 30 h)."""
import numpy as np
import pandas as pd
import pytest

from analyser_data.loader import TIME_COL, load_measurement
from analyser_data.plotting import format_axis_time


def _write(tmp_path, name, lines):
    path = tmp_path / name
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("minutes", [15, 180])
@pytest.mark.parametrize("period_ms", [100, 1000])
def test_sample_counter_uses_the_declared_period(tmp_path, minutes, period_ms):
    """Compteur « Sample » 1, 2, 3… + « Sampling,100ms » : 3 h et non 30 h (×10)."""
    n = minutes * 60 * 1000 // period_ms
    sampling = "{}ms".format(period_ms) if period_ms < 1000 else "1s"
    lines = ["Sampling,{}".format(sampling), "Sample,Courant (A)"]
    lines += ["{},{:.3f}".format(k + 1, np.sin(k / 50)) for k in range(n)]
    m = load_measurement(_write(tmp_path, "essai.csv", lines))
    assert m.duration_min == pytest.approx((n - 1) * period_ms / 60000)  # dernier point : (n - 1) périodes
    assert m.period_s == pytest.approx(period_ms / 1000)
    end = format_axis_time(m.data[TIME_COL].iloc[-1] + period_ms / 60000)
    assert end == "{}:{:02d}:00".format(minutes // 60, minutes % 60)  # 3:00:00, pas 30:00:00
    if period_ms < 1000:
        assert any("compteur d'échantillons" in w for w in m.warnings)


def test_counter_without_declared_period_is_flagged(tmp_path):
    lines = ["Point;U (V)"] + ["{};{}".format(k + 1, k % 7) for k in range(600)]
    m = load_measurement(_write(tmp_path, "sans_periode.csv", lines))
    assert any("1 point par seconde supposé" in w for w in m.warnings)


def test_integer_seconds_column_is_a_real_time(tmp_path):
    """« Temps (s) » en secondes entières : c'est un temps, pas un compteur (aucun message)."""
    lines = ["Sampling,100ms", "Temps (s);Courant (A)"] + ["{};{}".format(k, k % 5) for k in range(3600)]
    m = load_measurement(_write(tmp_path, "temps.csv", lines))
    assert m.duration_min == pytest.approx(59.98, abs=0.02)
    assert not any("compteur" in w for w in m.warnings)


def test_two_columns_with_decimal_comma_are_not_split_on_the_comma(tmp_path):
    """Régression : « Temps (s);Courant (A) » avec virgule décimale lu avec « , » comme séparateur."""
    lines = ["Temps (s);Courant (A)"] + ["{:.1f};{:.2f}".format(k * 0.1, k / 100).replace(".", ",")
                                         for k in range(9000)]
    m = load_measurement(_write(tmp_path, "deux_colonnes.csv", lines))
    assert [c.label for c in m.channels] == ["Courant (A)"]
    assert m.duration_min == pytest.approx(15, rel=1e-3) and m.period_s == pytest.approx(0.1)
    np.testing.assert_allclose(m.data["Courant (A)"].to_numpy()[:3], [0.0, 0.01, 0.02])


@pytest.mark.parametrize("minutes", [15, 180])
def test_graphtec_durations(tmp_path, minutes):
    n = minutes * 60 * 10
    t = pd.Timestamp("2026-07-10 09:00:00") + pd.to_timedelta(np.arange(n) * 0.1, unit="s")
    lines = ["Model,GL860", "Sampling,100ms", "Data", "No.,Date&Time,ms,CH1", "NO.,Time,ms,A"]
    lines += ["{},{},{:03d},{:+.3f}".format(k + 1, t[k].strftime("%Y-%m-%d %H:%M:%S"), t[k].microsecond // 1000,
                                             np.sin(k / 50)) for k in range(n)]
    m = load_measurement(_write(tmp_path, "gl860.CSV", lines))
    assert m.duration_min == pytest.approx(minutes, rel=1e-3)


def test_time_inconsistent_with_the_graphtec_header_is_rebuilt(tmp_path):
    """Données à 100 ms dont l'horodatage lu donne 10 fois trop (3 h -> 30 h) alors que
    l'en-tête annonce 108 000 points × 100 ms = 3 h, de 09:00:00 à 12:00:00."""
    n = 3 * 3600 * 10
    t = pd.Timestamp("2026-07-10 09:00:00") + pd.to_timedelta(np.arange(n), unit="s")  # 1 s au lieu de 0,1 s
    lines = ["Model,GL860", "Sampling,100ms", "Total data points,{}".format(n),
             "Start time,10/07/2026,09:00:00", "Stop time,10/07/2026,11:59:59", "Data",
             "No.,Date&Time,CH1", "NO.,Time,A"]
    lines += ["{},{},{:+.3f}".format(k + 1, t[k].strftime("%Y-%m-%d %H:%M:%S"), np.sin(k / 50)) for k in range(n)]
    m = load_measurement(_write(tmp_path, "gl860_3h.CSV", lines))
    assert m.duration_min == pytest.approx(180, rel=1e-3)  # 3 h, pas 30 h
    assert any("incohérent avec son en-tête" in w for w in m.warnings)


def test_consistent_header_changes_nothing():
    from pathlib import Path

    m = load_measurement(Path(__file__).resolve().parent.parent / "exemples" / "autres_formats" / "GL860_1s.CSV")
    assert m.duration_min == pytest.approx(669 / 60)
    assert not any("incohérent" in w for w in m.warnings)
