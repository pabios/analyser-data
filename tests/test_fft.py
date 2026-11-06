"""Issue #16 : spectre d'amplitude (FFT) des voies choisies."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyser_data.fft import amplitude_spectrum, format_frequency, format_period
from analyser_data.session import Session

EXAMPLES = Path(__file__).resolve().parent.parent / "exemples"


def test_sine_gives_its_frequency_and_amplitude():
    t_s = np.arange(0, 100, 0.01)  # 100 s à 100 Hz
    y = 3.0 + 2.0 * np.sin(2 * np.pi * 5.0 * t_s) + 0.5 * np.sin(2 * np.pi * 12.0 * t_s)
    sp = amplitude_spectrum(t_s / 60.0, y)
    assert sp.peak_hz == pytest.approx(5.0, rel=1e-3)
    assert sp.peak_amplitude == pytest.approx(2.0, rel=0.02)  # amplitude crête, dans l'unité de la voie
    assert sp.mean == pytest.approx(3.0, abs=1e-3)  # moyenne donnée à part, retirée du spectre
    assert sp.amplitude[0] == 0
    k12 = np.argmin(np.abs(sp.freq - 12.0))
    assert sp.amplitude[k12] == pytest.approx(0.5, rel=0.05)
    assert sp.filled == 0 and sp.points == len(t_s)


def test_missing_points_are_filled_and_counted():
    t_s = np.arange(0, 600, 1.0)
    y = np.sin(2 * np.pi * t_s / 60.0)  # période 1 min
    y[100:110] = np.nan
    keep = np.ones(len(t_s), bool)
    keep[300:330] = False  # interruption de 30 s
    sp = amplitude_spectrum(t_s[keep] / 60.0, y[keep])
    assert sp.filled == 10 + 30
    assert sp.peak_period_s == pytest.approx(60.0, rel=0.01)


def test_too_few_points():
    with pytest.raises(ValueError, match="Trop peu de points"):
        amplitude_spectrum([0, 1, 2], [1, 2, 3])


def test_formats():
    assert format_frequency(0.0123) == "12,3 mHz"
    assert format_frequency(5.0) == "5 Hz"
    assert format_frequency(1500.0) == "1,5 kHz"
    assert format_period(0.25) == "0,25 s"
    assert format_period(3 * 3600 + 20 * 60) == "3:20:00"
    assert format_frequency(None) == format_period(None) == "—"


def test_session_spectrum_uses_displayed_values_and_view():
    s = Session()
    s.load_files([str(EXAMPLES / "GL980_Mes-_260601-170139.CSV")])
    name = next(iter(s.measurements))
    key = (name, "Channel 4")
    whole = s.spectrum(key)
    assert whole.points == 24000 and whole.step_s == pytest.approx(0.1)
    part = s.spectrum(key, view=(5.0, 10.0))  # zone affichée : 5 min
    assert part.points == pytest.approx(3000, abs=2)
    # Les cycles du courant (période ~ 2 min 40) se retrouvent dans le pic principal
    assert 1 / whole.peak_hz == pytest.approx(s.spectrum(key).peak_period_s)


def test_spectrum_in_stretched_time_mode_uses_real_time():
    s = Session()
    s.load_files([str(EXAMPLES / "GL980_Mes-_260601-170139.CSV"), str(EXAMPLES / "nanodac_Rd_Z.txt")])
    gl = next(iter(s.measurements))
    before = s.spectrum((gl, "Channel 4"))
    s.set_time_mode("stretch")
    after = s.spectrum((gl, "Channel 4"))
    assert after.step_s == pytest.approx(before.step_s) and after.peak_hz == pytest.approx(before.peak_hz)
