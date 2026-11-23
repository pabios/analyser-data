"""Issue #16 : fichiers natifs Graphtec .GBD (en-tête texte Shift-JIS + données binaires big-endian)."""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyser_data.loader import TIME_COL, load_measurement

HEADER_SIZE = 8192


def make_gbd(path, n=600, sample="100ms", counts=None, amp=None, start="2026-10-29, 09:46:10"):
    order = ["CH1", "CH2", "CH3", "Alarm", "AlOut", "Pulse1"]
    amp = amp or {"CH1": "DC, 20V, Off", "CH2": "DC, 200mV, Off", "CH3": "TEMP, TC_K, Off"}
    lines = ["$Common", "ID         = 261D1118", "Volume     = 1, 1", "HeaderSiz  = {}".format(HEADER_SIZE),
             "Model      = GL860", "$$Data", "Counts     = {}".format(n if counts is None else counts),
             "Order      = " + ", ".join(order), "Sample     = {}".format(sample), "TempUnit   = ﾟC",
             "$$Time", "Start      = {}".format(start), "Trigger    = {}".format(start),
             "$Amp"] + ["{} = {}".format(k, v) for k, v in amp.items()] + ["$EndHeader"]
    header = "\r\n".join(lines).encode("shift_jis")
    header += b"\x00" * (HEADER_SIZE - len(header))
    k = np.arange(n)
    ch1 = np.round(12.0 + 5 * np.sin(k / 30), 3)  # V, gamme 20 V -> brut = V × 1000
    ch2 = np.round(50 * np.cos(k / 40), 2)  # mV, gamme 200 mV -> brut = mV × 100
    ch3 = np.round(21.5 + k / 100, 1)  # °C -> brut = °C × 10
    dtype = np.dtype([("CH1", ">i2"), ("CH2", ">i2"), ("CH3", ">i2"), ("Alarm", ">u2"), ("AlOut", ">u2"),
                      ("Pulse1", ">u4")])
    records = np.zeros(n, dtype=dtype)
    records["CH1"] = np.round(ch1 * 1000)
    records["CH2"] = np.round(ch2 * 100)
    records["CH3"] = np.round(ch3 * 10)
    records["CH3"][10] = 32767  # hors échelle (BURNOUT)
    records["Pulse1"] = k
    path.write_bytes(header + records.tobytes())
    return ch1, ch2, ch3


def test_gbd_is_decoded_like_the_csv_export(tmp_path):
    path = tmp_path / "261029-094610.GBD"
    ch1, ch2, ch3 = make_gbd(path)
    m = load_measurement(path)
    assert m.source == "Graphtec" and m.separator == "GBD"
    assert [c.label for c in m.channels] == ["Channel 1", "Channel 2", "Channel 3"]
    assert [c.unit for c in m.channels] == ["V", "mV", "°C"]
    np.testing.assert_allclose(m.data["Channel 1"].to_numpy(), ch1, atol=1e-9)
    np.testing.assert_allclose(m.data["Channel 2"].to_numpy(), ch2, atol=1e-9)
    temperature = m.data["Channel 3"].to_numpy()
    assert np.isnan(temperature[10])  # hors échelle laissé vide
    np.testing.assert_allclose(np.delete(temperature, 10), np.delete(ch3, 10), atol=1e-9)
    assert m.period_s == pytest.approx(0.1) and m.duration_min == pytest.approx(599 * 0.1 / 60)
    assert m.start == pd.Timestamp("2026-10-29 09:46:10")
    assert any(".GBD" in w for w in m.warnings)


def test_gbd_header_seen_in_the_client_screenshot_is_recognised_without_extension(tmp_path):
    path = tmp_path / "enregistrement.dat"
    make_gbd(path)
    assert len(load_measurement(path).channels) == 3


def test_truncated_gbd_is_reported(tmp_path):
    path = tmp_path / "coupe.GBD"
    make_gbd(path, n=300, counts=1000)
    m = load_measurement(path)
    assert len(m.data) == 300 and any("incomplet" in w for w in m.warnings)


# --- Mise à l'échelle de l'appareil (« Scaling », $Measure / $$Scale) : en-tête réel du client ---

CLIENT_HEADER = Path(__file__).resolve().parent / "fixtures" / "gl980_issue16_header.txt"


def make_client_gbd(path, n=200, scale_overrides=None):
    """GL980 du client (issue #16) : CH1 et CH2 en A par mise à l'échelle (shunts), CH6 en V.
    En-tête copié tel quel de son « Extrait anonymisé » ; seules les mesures sont inventées."""
    text = CLIENT_HEADER.read_text(encoding="utf-8")
    text = re.sub(r"Counts\s*=\s*\d+", "Counts      = {}".format(n), text)
    for old, new in (scale_overrides or {}).items():
        text = text.replace(old, new)
    header = text.replace("\n", "\r\n").encode("shift_jis")
    header += b"\x00" * (8192 - len(header))
    k = np.arange(n)
    dtype = np.dtype([("CH1", ">i2"), ("CH2", ">i2"), ("CH6", ">i2"), ("Alarm", ">u2"), ("AlarmOut", ">u2")])
    assert dtype.itemsize == 10  # OneDataSize = 10
    records = np.zeros(n, dtype=dtype)
    records["CH1"] = 10000 + (k % 50) * 100  # 50 mV et plus, gamme 100 mV
    records["CH2"] = 5000 - (k % 20) * 10  # 25 mV et moins
    records["CH6"] = 2400 + (k % 10)  # ≈ 12 V, gamme 100 V
    path.write_bytes(header + records.tobytes())
    return records


def test_client_gl980_scaled_channels_are_in_amperes(tmp_path):
    path = tmp_path / "essai.GBD"
    records = make_client_gbd(path)
    m = load_measurement(path)
    assert [c.label for c in m.channels] == ["Channel 1", "Channel 2", "Channel 6"]
    assert [c.unit for c in m.channels] == ["A", "A", "V"]
    # CH1 : 0–20 000 points (0–100 mV) -> 0–100,00 A ; CH2 : 0–10 000 points (0–50 mV) -> 0–20,00 A
    np.testing.assert_allclose(m.data["Channel 1"].to_numpy(), records["CH1"] / 200.0)
    np.testing.assert_allclose(m.data["Channel 2"].to_numpy(), records["CH2"] / 500.0)
    np.testing.assert_allclose(m.data["Channel 6"].to_numpy(), records["CH6"].astype(float) * 100 / 20000.0)
    assert m.data["Channel 1"].iloc[0] == pytest.approx(50.0)
    assert m.data["Channel 2"].iloc[0] == pytest.approx(10.0)
    assert m.data["Channel 6"].iloc[0] == pytest.approx(12.0)
    assert m.period_s == pytest.approx(0.1)
    assert m.start == pd.Timestamp("2026-11-09 16:48:09")
    notes = [w for w in m.warnings if "mise à l'échelle" in w]
    assert notes == ["CH1 : mise à l'échelle de l'appareil appliquée (0 à 100 mV -> 0 à 100 A).",
                     "CH2 : mise à l'échelle de l'appareil appliquée (0 à 50 mV -> 0 à 20 A)."]


def test_scaling_with_offset_4_20_mA(tmp_path):
    """Mise à l'échelle avec décalage (1–5 V -> 4–20 mA, comme CH4 du client) appliquée à CH6."""
    path = tmp_path / "essai.GBD"
    records = make_client_gbd(path, scale_overrides={
        'CH6         = Off,    +200,   +1000,    +400,   +2000,  +2, "mA"':
        'CH6         = On ,    +200,   +1000,    +400,   +2000,  +2, "mA"'})
    m = load_measurement(path)
    assert m.channels[2].unit == "mA"
    # 100 V / 20 000 points : 200 points = 1 V -> 4 mA, 1 000 points = 5 V -> 20 mA
    expected = (400 + (records["CH6"].astype(float) - 200) * 1600 / 800) / 100
    np.testing.assert_allclose(m.data["Channel 6"].to_numpy(), expected)


def test_scaling_off_keeps_volts_and_calculation_is_reported(tmp_path):
    path = tmp_path / "essai.GBD"
    make_client_gbd(path, scale_overrides={
        'CH1         = On ,      +0,  +20000': 'CH1         = Off,      +0,  +20000',
        'CH2         = Off, CH2  , ADD': 'CH2         = On , CH2  , ADD'})
    m = load_measurement(path)
    assert [c.unit for c in m.channels] == ["mV", "A", "V"]
    assert any("CH2 : calcul entre voies" in w for w in m.warnings)
