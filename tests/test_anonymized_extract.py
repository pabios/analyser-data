"""Extrait anonymisé : le client peut envoyer la structure de son fichier sans ses mesures."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyser_data.loader import load_measurement, write_anonymized_extract

EXAMPLES = Path(__file__).resolve().parent.parent / "exemples"


def _clim(tmp_path):
    t = pd.Timestamp("2026-10-29 09:46:10") + pd.to_timedelta(np.arange(120) * 60, unit="s")
    lines = ["Simpati 4.80 Export", "Date;Heure;T consigne (°C);T mesure (°C);HR mesure (%)"]
    lines += ["{};{};60,0;{};{}".format(x.strftime("%d/%m/%Y"), x.strftime("%H:%M:%S"),
                                       str(round(23 + i * 0.37 % 5, 2)).replace(".", ","),
                                       str(round(45 + i * 0.73 % 9, 1)).replace(".", ","))
              for i, x in enumerate(t)]
    path = tmp_path / "chaud initial.csv"
    path.write_text("\r\n".join(lines) + "\r\n", encoding="cp1252")
    return path


@pytest.mark.parametrize("source", ["clim", "gl980", "nanodac"])
def test_extract_keeps_the_format_but_not_the_values(tmp_path, source):
    path = {"clim": lambda: _clim(tmp_path), "gl980": lambda: EXAMPLES / "GL980_Mes-_260601-170139.CSV",
            "nanodac": lambda: EXAMPLES / "nanodac_Rd_Z.txt"}[source]()
    original = load_measurement(path)
    extract = write_anonymized_extract(path, tmp_path / "extrait.csv")
    text = extract.read_text(encoding="utf-8")
    assert text.startswith("# Extrait anonymisé")
    copy = load_measurement(extract)
    assert [(c.label, c.unit) for c in copy.channels] == [(c.label, c.unit) for c in original.channels]
    assert copy.period_s == pytest.approx(original.period_s, rel=1e-3)  # temps conservé
    if original.start is not None:
        assert copy.start == original.start
    n = min(20, len(copy.data))
    measured = [c.label for c in original.channels]
    varying = [c for c in measured if original.data[c].iloc[:n].nunique() > 2]
    for c in varying:  # les vraies mesures ne sont pas dans l'extrait
        assert not np.allclose(copy.data[c].iloc[:n].to_numpy(), original.data[c].iloc[:n].to_numpy())


def test_gbd_extract_keeps_only_the_header(tmp_path):
    from test_gbd import make_gbd

    path = tmp_path / "essai.GBD"
    make_gbd(path)
    text = write_anonymized_extract(path, tmp_path / "extrait.txt").read_text(encoding="utf-8")
    assert "HeaderSiz" in text and "$Amp" in text and "\x00" not in text
