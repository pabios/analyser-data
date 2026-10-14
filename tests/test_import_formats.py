"""Issue #10 : import CSV / TXT / Excel (.xlsx, .xls), choix de la feuille, séparateur imposé."""
import builtins
import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from analyser_data.excel import sheet_names
from analyser_data.loader import SPACE, TIME_COL, LoadError, load_measurement

EXAMPLES = Path(__file__).resolve().parent.parent / "exemples"
START = dt.datetime(2026, 7, 10, 9, 44, 13)


def _rows(n=120):
    """Classeur comme un export Graphtec ouvert dans Excel : préambule, en-têtes, unités,
    dates / heures en vraies cellules date, valeurs numériques."""
    rows = [["Model", "GL860"], ["Sampling", "1s"], [], ["Data"],
            ["No.", "Date&Time", "ms", "CH1", "CH2", "CH5"], ["NO.", "Time", "ms", "V", "mA", "°C"]]
    for k in range(n):
        rows.append([k + 1, START + dt.timedelta(seconds=k), 0, 24.0 + 0.01 * k, 100.0 * np.sin(k / 10), 21.5])
    return rows


def _xlsx(path, sheets):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    book.save(path)
    return path


def _check(m, n=120):
    assert len(m.data) == n
    assert [c.label for c in m.channels] == ["Channel 1", "Channel 2", "Température ambiante"] or \
        [c.label for c in m.channels] == ["Channel 1", "Channel 2", "Channel 5"]
    assert [c.unit for c in m.channels] == ["V", "mA", "°C"]
    np.testing.assert_allclose(m.data[TIME_COL].to_numpy(), np.arange(n) / 60.0, atol=1e-9)
    assert m.start == np.datetime64(START)
    np.testing.assert_allclose(m.data["Channel 1"].to_numpy(), 24.0 + 0.01 * np.arange(n))


def test_xlsx_with_openpyxl(tmp_path):
    m = load_measurement(_xlsx(tmp_path / "essai.xlsx", {"Mesures": _rows()}))
    _check(m)
    assert m.sheet == "Mesures" and m.path.suffix == ".xlsx"


def test_xlsx_without_openpyxl(tmp_path, monkeypatch):
    """Lecteur intégré (zip + XML) si openpyxl n'est pas installé : mêmes valeurs, dates comprises."""
    path = _xlsx(tmp_path / "essai.xlsx", {"Mesures": _rows()})
    real_import = builtins.__import__

    def no_openpyxl(name, *args, **kwargs):
        if name == "openpyxl" or name.startswith("openpyxl."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_openpyxl)
    assert sheet_names(path) == ["Mesures"]
    _check(load_measurement(path))


def test_xls(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    book = xlwt.Workbook()
    sheet = book.add_sheet("Mesures")
    date_style = xlwt.easyxf(num_format_str="yyyy-mm-dd hh:mm:ss")
    for r, row in enumerate(_rows()):
        for c, value in enumerate(row):
            if isinstance(value, dt.datetime):
                sheet.write(r, c, value, date_style)
            else:
                sheet.write(r, c, value)
    path = tmp_path / "essai.xls"
    book.save(str(path))
    _check(load_measurement(path))


def test_choose_the_sheet(tmp_path):
    path = _xlsx(tmp_path / "classeur.xlsx", {"Notes": [["Essai du 10/07"], ["Opérateur", "X"]],
                                              "Mesures": _rows(30)})
    assert sheet_names(path) == ["Notes", "Mesures"]
    with pytest.raises(LoadError):
        load_measurement(path)  # 1re feuille : pas de mesures
    m = load_measurement(path, sheet="Mesures")
    _check(m, 30)
    assert "Mesures" in m.warnings[0]


def test_space_separated_columns(tmp_path):
    lines = ["Temps(s)   U(V)    I(A)"] + ["{:8.1f} {:7.3f} {:7.3f}".format(k * 0.5, 12 + k / 100, k / 50)
                                          for k in range(40)]
    path = tmp_path / "aligne.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    m = load_measurement(path)  # détecté en dernier recours
    assert [c.unit for c in m.channels] == ["V", "A"] and len(m.data) == 40
    assert m.data[TIME_COL].iloc[-1] == pytest.approx(39 * 0.5 / 60)
    m2 = load_measurement(path, sep=SPACE)  # ou imposé
    assert len(m2.data) == 40


def test_forced_separator(tmp_path):
    m = load_measurement(EXAMPLES / "GL980_Mes-_260601-170139.CSV", sep=",")
    assert len(m.channels) == 6
    with pytest.raises(LoadError):
        load_measurement(EXAMPLES / "GL980_Mes-_260601-170139.CSV", sep="\t")


@pytest.mark.parametrize("name", ["rapport.pdf", "notes.docx", "photo.png"])
def test_unsupported_format_is_refused_clearly(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 ...")
    with pytest.raises(LoadError, match="pas pris en charge.*XLSX"):
        load_measurement(path)
