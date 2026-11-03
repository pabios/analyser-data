"""Issue #16 : fichiers natifs Graphtec .GBD (en-tête texte Shift-JIS + données binaires big-endian)."""
import numpy as np
import pandas as pd
import pytest

from analyser_data.loader import TIME_COL, load_measurement

HEADER_SIZE = 8192


def make_gbd(path, n=600, sample="100ms", counts=None, amp=None):
    order = ["CH1", "CH2", "CH3", "Alarm", "AlOut", "Pulse1"]
    amp = amp or {"CH1": "DC, 20V, Off", "CH2": "DC, 200mV, Off", "CH3": "TEMP, TC_K, Off"}
    lines = ["$Common", "ID         = 261D1118", "Volume     = 1, 1", "HeaderSiz  = {}".format(HEADER_SIZE),
             "Model      = GL860", "$$Data", "Counts     = {}".format(n if counts is None else counts),
             "Order      = " + ", ".join(order), "Sample     = {}".format(sample), "TempUnit   = ﾟC",
             "$$Time", "Start      = 2026-10-29, 09:46:10", "Trigger    = 2026-10-29, 09:46:10",
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
