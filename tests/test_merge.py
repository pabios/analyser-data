"""Issue #19 : fusion chronologique de plusieurs fichiers CSV en un seul (règles validées par le client)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyser_data.loader import load_measurement
from analyser_data.merge import MergeError, date_shape, merge_files, merge_summary
from analyser_data.session import Session

from test_gbd import make_gbd

NANODAC_HEAD = ("Date/Heure\tChannel 1       (ENAN1);Group 1;M401-T120;°C\t"
                "Channel 2       (ENAN2);Group 1;M402-H120;%HR")


def nanodac(path, start, n, step_s=10, temp0=20.0, extra=None):
    t0 = pd.Timestamp(start)
    rows = ["{}\t{:.1f}\t{:.1f}".format((t0 + pd.Timedelta(seconds=i * step_s)).strftime("%d/%m/%y %H:%M:%S"),
                                        temp0 + i / 10, 50 + i % 7).replace(".", ",") for i in range(n)]
    path.write_bytes(("\r\n".join([NANODAC_HEAD] + rows + (extra or [])) + "\r\n").encode("cp1252"))
    return rows


def graphtec(path, start, n, model="GL860"):
    t0 = pd.Timestamp(start)
    lines = ["Vendor,GRAPHTEC Corporation", "Model,{}".format(model), "Sampling,100ms",
             "Total data points,{}".format(n), "Data", "No.,Date&Time,ms,CH1,CH2", "NO.,Time,ms,V,A"]
    rows = []
    for j in range(n):
        moment = t0 + pd.Timedelta(milliseconds=100 * j)
        rows.append("{},{},{},{:+.3f},{:+.2f}".format(j + 1, moment.strftime("%Y/%m/%d %H:%M:%S"),
                                                     moment.microsecond // 1000, 12 + j / 1000, j / 100))
    path.write_bytes(("\r\n".join(lines + rows) + "\r\n").encode("cp1252"))
    return lines, rows


def read_lines(path, encoding="cp1252"):
    return path.read_bytes().decode(encoding).split("\r\n")[:-1]


def test_files_are_merged_in_chronological_order_whatever_the_selection(tmp_path):
    a = nanodac(tmp_path / "zz_matin.txt", "2026-11-20 08:00:00", 30)
    b = nanodac(tmp_path / "aa_midi.txt", "2026-11-20 08:05:00", 30, temp0=25)
    c = nanodac(tmp_path / "mm_soir.txt", "2026-11-20 08:10:00", 30, temp0=30)
    out = tmp_path / "fusion.txt"
    result = merge_files([tmp_path / "mm_soir.txt", tmp_path / "zz_matin.txt", tmp_path / "aa_midi.txt"], out)
    assert [p.name for p in result.files] == ["zz_matin.txt", "aa_midi.txt", "mm_soir.txt"]
    lines = read_lines(out)
    assert lines == [NANODAC_HEAD] + a + b + c  # une seule ligne de titres, lignes recopiées telles quelles
    assert result.rows == 90 and result.duplicates == 0
    assert out.read_bytes().count(b"\r\n") == 91  # fins de ligne Windows conservées
    assert "°C".encode("cp1252") in out.read_bytes()  # encodage Windows conservé (pas d'UTF-8 imposé)
    assert result.start == pd.Timestamp("2026-11-20 08:00:00")
    assert result.end == pd.Timestamp("2026-11-20 08:14:50")
    assert not result.notes
    assert "3 fichiers" in merge_summary(result) and "90 lignes" in merge_summary(result)

    m = load_measurement(out)  # le fichier fusionné se relit comme un seul essai continu
    assert len(m.data) == 90 and m.period_s == pytest.approx(10.0)
    assert m.duration_min == pytest.approx(89 * 10 / 60)


def test_interleaved_rows_are_sorted_strictly(tmp_path):
    """Deux fichiers qui se chevauchent : lignes rangées par date-heure, chevauchement signalé."""
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 20, step_s=20)
    nanodac(tmp_path / "b.txt", "2026-11-20 08:00:10", 20, step_s=20, temp0=40)
    out = tmp_path / "f.txt"
    result = merge_files([tmp_path / "b.txt", tmp_path / "a.txt"], out)
    stamps = pd.to_datetime([line.split("\t")[0] for line in read_lines(out)[1:]], format="%d/%m/%y %H:%M:%S")
    assert stamps.is_monotonic_increasing and len(stamps) == 40
    assert any("se chevauchent" in n for n in result.notes)


def test_identical_rows_kept_once(tmp_path):
    a = nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30)
    nanodac(tmp_path / "b.txt", "2026-11-20 08:00:00", 40)  # mêmes 30 premières lignes + 10 nouvelles
    out = tmp_path / "f.txt"
    result = merge_files([tmp_path / "a.txt", tmp_path / "b.txt"], out)
    assert result.duplicates == 30 and result.rows == 40
    assert read_lines(out)[1:31] == a
    assert any("30 ligne(s) en double" in n for n in result.notes)
    assert any("« b.txt » reprend la fin de « a.txt »" in n for n in result.notes)
    assert not any("se chevauchent" in n for n in result.notes)


def test_example_parts_in_any_order(tmp_path):
    folder = Path(__file__).resolve().parent.parent / "exemples" / "fusion"
    out = tmp_path / "fusion.txt"
    result = merge_files([folder / "enregistrement_3.txt", folder / "enregistrement_1.txt",
                          folder / "enregistrement_2.txt"], out)
    assert [p.name for p in result.files] == ["enregistrement_1.txt", "enregistrement_2.txt", "enregistrement_3.txt"]
    assert result.rows == 1620 and result.duplicates == 30
    assert any(n.startswith("Trou de 0:30:10") for n in result.notes)
    m = load_measurement(out)
    assert len(m.data) == 1620 and m.period_s == pytest.approx(10.0)


def test_same_time_different_values_both_kept_in_file_order(tmp_path):
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 10, temp0=20)
    nanodac(tmp_path / "b.txt", "2026-11-20 08:01:30", 10, temp0=99)  # 08:01:30 existe aussi dans a
    out = tmp_path / "f.txt"
    result = merge_files([tmp_path / "b.txt", tmp_path / "a.txt"], out)
    lines = read_lines(out)
    at = [line for line in lines if line.startswith("20/11/26 08:01:30")]
    assert len(at) == 2 and at[0].split("\t")[1] == "20,9" and at[1].split("\t")[1] == "99,0"
    assert result.rows == 20
    assert any("même date-heure" in n and "20/11/2026 08:01:30" in n for n in result.notes)


def test_gap_between_files_is_kept_and_reported(tmp_path):
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30)
    nanodac(tmp_path / "b.txt", "2026-11-20 10:00:00", 30)
    out = tmp_path / "f.txt"
    result = merge_files([tmp_path / "a.txt", tmp_path / "b.txt"], out)
    assert result.rows == 60
    assert any(n.startswith("Trou de 1:55:10") for n in result.notes)


def test_graphtec_preamble_of_oldest_file_and_no_column_ignored_for_duplicates(tmp_path):
    head_a, rows_a = graphtec(tmp_path / "essai_2.csv", "2026-11-20 09:00:00", 50)
    head_b, rows_b = graphtec(tmp_path / "essai_1.csv", "2026-11-20 08:59:58", 50)
    out = tmp_path / "f.csv"
    result = merge_files([tmp_path / "essai_2.csv", tmp_path / "essai_1.csv"], out)
    lines = read_lines(out)
    assert lines[:7] == head_b  # bloc d'en-tête du plus ancien, une seule fois
    # 09:00:00.000 à 09:00:02.900 dans les deux fichiers : mêmes valeurs ? non (CH dépend du n° de ligne)
    assert len(lines) == 7 + 100 - result.duplicates
    assert any("En-tête recopié de « essai_1.csv »" in n for n in result.notes)
    m = load_measurement(out)
    assert m.period_s == pytest.approx(0.1)
    assert m.start == pd.Timestamp("2026-11-20 08:59:58")


def test_graphtec_overlap_with_identical_values_but_other_row_numbers(tmp_path):
    """Le n° de ligne (« No. ») recommence à 1 dans chaque fichier : il n'empêche pas de voir le doublon."""
    _head, rows = graphtec(tmp_path / "a.csv", "2026-11-20 09:00:00", 40)
    lines = (tmp_path / "a.csv").read_bytes().decode("cp1252").split("\r\n")
    renumbered = ["{},{}".format(i + 1, r.split(",", 1)[1]) for i, r in enumerate(rows[20:])]
    (tmp_path / "b.csv").write_bytes(("\r\n".join(lines[:7] + renumbered) + "\r\n").encode("cp1252"))
    result = merge_files([tmp_path / "a.csv", tmp_path / "b.csv"], tmp_path / "f.csv")
    assert result.duplicates == 20 and result.rows == 40


@pytest.mark.parametrize("change, message", [
    (lambda h: h.replace("\tChannel 2       (ENAN2);Group 1;M402-H120;%HR", ""), "voie(s) absente(s)"),
    (lambda h: h + "\tChannel 3", "voie(s) en plus"),
])
def test_incompatible_columns_block_the_merge(tmp_path, change, message):
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30)
    rows = nanodac(tmp_path / "b.txt", "2026-11-20 09:00:00", 30)
    head = change(NANODAC_HEAD)
    n = head.count("\t")
    body = ["\t".join(r.split("\t")[:n + 1] + ["1,0"] * (n + 1 - len(r.split("\t")))) for r in rows]
    (tmp_path / "b.txt").write_bytes(("\r\n".join([head] + body) + "\r\n").encode("cp1252"))
    out = tmp_path / "f.txt"
    with pytest.raises(MergeError) as err:
        merge_files([tmp_path / "a.txt", tmp_path / "b.txt"], out)
    assert "« b.txt »" in str(err.value) and message in str(err.value)
    assert not out.exists()


def test_other_date_format_blocks_the_merge(tmp_path):
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30)
    rows = nanodac(tmp_path / "b.txt", "2026-11-20 09:00:00", 30)
    iso = [pd.to_datetime(r.split("\t")[0], format="%d/%m/%y %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S") + "\t"
           + r.split("\t", 1)[1] for r in rows]
    (tmp_path / "b.txt").write_bytes(("\r\n".join([NANODAC_HEAD] + iso) + "\r\n").encode("cp1252"))
    with pytest.raises(MergeError, match="b.txt.*format de date"):
        merge_files([tmp_path / "a.txt", tmp_path / "b.txt"], tmp_path / "f.txt")


def test_other_separator_blocks_the_merge(tmp_path):
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30)
    rows = nanodac(tmp_path / "b.txt", "2026-11-20 09:00:00", 30)
    (tmp_path / "b.txt").write_bytes(("\r\n".join([NANODAC_HEAD.replace("\t", ";;").replace(";;", "|")]
                                                  + [r.replace("\t", "|") for r in rows]) + "\r\n").encode("cp1252"))
    with pytest.raises(MergeError, match="b.txt.*séparateur"):
        merge_files([tmp_path / "a.txt", tmp_path / "b.txt"], tmp_path / "f.txt")


def test_corrupted_line_is_removed_and_reported(tmp_path):
    """Retour client (#19) : une ligne « USBCS°Œ » au milieu d'un CSV Graphtec bloquait toute la fusion."""
    _lines, a = graphtec(tmp_path / "a.CSV", "2026-10-23 08:00:00", 50)
    lines, rows = graphtec(tmp_path / "b.CSV", "2026-10-23 08:00:10", 50)
    rows[20] = "USBCS°Œ,ÿÿ,Ÿ"
    (tmp_path / "b.CSV").write_bytes(("\r\n".join(lines + rows) + "\r\n").encode("cp1252"))
    out = tmp_path / "f.CSV"
    result = merge_files([tmp_path / "a.CSV", tmp_path / "b.CSV"], out)
    assert result.rows == 99
    assert not any("USBCS" in line for line in read_lines(out))
    note = next(n for n in result.notes if "corrompue" in n)
    assert "« b.CSV » : 1 ligne(s)" in note and "ligne {}".format(len(lines) + 21) in note and "USBCS" in note
    m = load_measurement(out)
    assert len(m.data) == 99


def test_many_unreadable_dates_still_block_the_merge_with_line_number(tmp_path):
    """Beaucoup de dates illisibles : format mal compris, pas une ligne abîmée -> rien n'est écrit."""
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30)
    rows = nanodac(tmp_path / "b.txt", "2026-11-20 09:00:00", 400)
    for k in range(4, 400, 16):  # 25 lignes illisibles sur 400
        rows[k] = "pas une date\t21,0\t50,0"
    (tmp_path / "b.txt").write_bytes(("\r\n".join([NANODAC_HEAD] + rows) + "\r\n").encode("cp1252"))
    with pytest.raises(MergeError) as err:
        merge_files([tmp_path / "a.txt", tmp_path / "b.txt"], tmp_path / "f.txt")
    assert "« b.txt », ligne 6" in str(err.value) and "date-heure illisible" in str(err.value)
    assert not (tmp_path / "f.txt").exists()


def test_graphtec_dates_without_leading_zeros_have_the_same_format(tmp_path):
    """Retour client (#19) : « 2026/10/1 09:21:19 » refusé face à « 2026/9/21 16:47:49 »."""
    def write(name, start, n=20):
        t0 = pd.Timestamp(start)
        head = ["Vendor,GRAPHTEC Corporation", "Model,GL980", "Data", "No.,Date&Time,ms,CH1", "NO.,Time,ms,V"]
        rows = []
        for j in range(n):
            t = t0 + pd.Timedelta(seconds=j)
            rows.append("{},{}/{}/{} {:02d}:{:02d}:{:02d},0,+1.000".format(j + 1, t.year, t.month, t.day, t.hour,
                                                                           t.minute, t.second))
        (tmp_path / name).write_bytes(("\r\n".join(head + rows) + "\r\n").encode("cp1252"))
    write("Mes-_260921-164742.CSV", "2026-09-21 16:47:49")
    write("Mes-_261001-092111.CSV", "2026-10-01 09:21:19")
    write("Mes-_261016-125639.CSV", "2026-10-16 12:56:42")
    out = tmp_path / "f.CSV"
    result = merge_files(sorted(tmp_path.glob("Mes-*.CSV")), out)
    assert result.rows == 60
    assert result.start == pd.Timestamp("2026-09-21 16:47:49")
    assert result.end == pd.Timestamp("2026-10-16 12:57:01")


def test_date_shape_ignores_missing_leading_zeros_but_not_field_order():
    assert date_shape("2026/9/21 16:47:49 0") == date_shape("2026/10/1 09:21:19 0")
    assert date_shape("21/09/26 16:47:49") == date_shape("1/9/26 6:47:49")
    assert date_shape("2026/9/21 16:47:49") != date_shape("21/9/2026 16:47:49")
    assert date_shape("2026-09-21 16:47:49") != date_shape("2026/09/21 16:47:49")


def test_elapsed_time_only_cannot_be_ordered(tmp_path):
    for name, first in (("a.csv", 0), ("b.csv", 1000)):
        lines = ["Temps;U (V)"] + ["{};{}".format(first + i * 100, "1,5") for i in range(30)]
        (tmp_path / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(MergeError, match="temps écoulé seulement") as err:
        merge_files([tmp_path / "a.csv", tmp_path / "b.csv"], tmp_path / "f.csv")
    assert ".GBD" in str(err.value)  # piste donnée au client : les .GBD ont l'heure de début


def test_clock_going_back_is_reported_not_corrected(tmp_path):
    """Passage à l'heure d'hiver : 02:00–03:00 deux fois dans le même fichier."""
    t = list(pd.date_range("2026-10-25 02:50:00", periods=10, freq="1min"))
    t += list(pd.date_range("2026-10-25 02:00:00", periods=10, freq="1min"))
    rows = ["{}\t20,0\t50,0".format(x.strftime("%d/%m/%y %H:%M:%S")) for x in t]
    (tmp_path / "a.txt").write_bytes(("\r\n".join([NANODAC_HEAD] + rows) + "\r\n").encode("cp1252"))
    nanodac(tmp_path / "b.txt", "2026-10-25 04:00:00", 10)
    result = merge_files([tmp_path / "a.txt", tmp_path / "b.txt"], tmp_path / "f.txt")
    assert result.rows == 30
    assert any("l'heure recule à 25/10/2026 02:00:00" in n for n in result.notes)


def test_trailing_lines_without_measure_are_reported(tmp_path):
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30, extra=["End of data"])
    nanodac(tmp_path / "b.txt", "2026-11-20 09:00:00", 30)
    result = merge_files([tmp_path / "a.txt", tmp_path / "b.txt"], tmp_path / "f.txt")
    assert result.rows == 60
    assert any("« a.txt » : 1 ligne(s) sans mesure" in n and "End of data" in n for n in result.notes)


def test_utf8_bom_and_lf_preserved(tmp_path):
    for name, start in (("a.csv", "2026-11-20 08:00:00"), ("b.csv", "2026-11-20 08:01:00")):
        t0 = pd.Timestamp(start)
        lines = ["Date;Température (°C)"] + ["{};{}".format((t0 + pd.Timedelta(seconds=i)).strftime(
            "%d/%m/%Y %H:%M:%S"), "21,5") for i in range(60)]
        (tmp_path / name).write_bytes(("\n".join(lines) + "\n").encode("utf-8-sig"))
    out = tmp_path / "f.csv"
    merge_files([tmp_path / "b.csv", tmp_path / "a.csv"], out)
    data = out.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf") and data.count(b"\xef\xbb\xbf") == 1
    assert b"\r\n" not in data and data.count(b"\n") == 121


@pytest.mark.parametrize("paths, message", [
    (["a.txt"], "au moins deux"),
    (["a.txt", "a.txt"], "deux fois"),
])
def test_selection_errors(tmp_path, paths, message):
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30)
    with pytest.raises(MergeError, match=message):
        merge_files([tmp_path / p for p in paths], tmp_path / "f.txt")


def test_output_cannot_overwrite_a_source(tmp_path):
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30)
    nanodac(tmp_path / "b.txt", "2026-11-20 09:00:00", 30)
    with pytest.raises(MergeError, match="remplacer"):
        merge_files([tmp_path / "a.txt", tmp_path / "b.txt"], tmp_path / "a.txt")


def test_excel_files_are_refused(tmp_path):
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30)
    (tmp_path / "b.xlsx").write_bytes(b"PK\x03\x04")
    with pytest.raises(MergeError, match="b.xlsx.*fichiers texte"):
        merge_files([tmp_path / "a.txt", tmp_path / "b.xlsx"], tmp_path / "f.txt")


def test_merged_file_opens_as_one_measurement_in_session(tmp_path):
    nanodac(tmp_path / "a.txt", "2026-11-20 08:00:00", 30)
    nanodac(tmp_path / "b.txt", "2026-11-20 08:05:00", 30)
    out = tmp_path / "fusion.txt"
    result = merge_files([tmp_path / "a.txt", tmp_path / "b.txt"], out)
    s = Session()
    s.log(merge_summary(result))
    loaded, errors = s.load_files([out])
    assert not errors and len(s.measurements) == 1
    m = loaded[0]
    assert [ch.unit for ch in m.channels] == ["°C", "%HR"]
    assert np.all(np.diff(m.elapsed_min) > 0)
    assert any("Fusion chronologique de 2 fichiers (a.txt, b.txt) dans fusion.txt : 60 lignes" in text
               for _t, text in s.journal)


# --- Fichiers natifs Graphtec .GBD (retour client sur #19) ---------------------------------------

def test_gbd_files_are_merged_in_chronological_order(tmp_path):
    a = make_gbd(tmp_path / "261029-094610.GBD", n=300, start="2026-10-29, 09:46:10")
    b = make_gbd(tmp_path / "261029-095110.GBD", n=300, start="2026-10-29, 09:46:40")  # juste après a
    out = tmp_path / "fusion.csv"
    result = merge_files([tmp_path / "261029-095110.GBD", tmp_path / "261029-094610.GBD"], out)
    assert [p.name for p in result.files] == ["261029-094610.GBD", "261029-095110.GBD"]
    assert result.rows == 600 and result.duplicates == 0
    assert result.start == pd.Timestamp("2026-10-29 09:46:10")
    assert result.end == pd.Timestamp("2026-10-29 09:47:09.900")
    assert any(".GBD" in n and "mise à l'échelle" in n for n in result.notes)
    assert not any("Trou" in n for n in result.notes)

    m = load_measurement(out)  # relu comme un export Graphtec : mêmes voies, unités, valeurs
    assert m.source == "Graphtec"
    assert [c.label for c in m.channels] == ["Channel 1", "Channel 2", "Channel 3"]
    assert [c.unit for c in m.channels] == ["V", "mV", "°C"]
    assert len(m.data) == 600 and m.period_s == pytest.approx(0.1)
    assert m.start == pd.Timestamp("2026-10-29 09:46:10")
    np.testing.assert_allclose(m.data["Channel 1"].to_numpy(), np.concatenate([a[0], b[0]]), atol=1e-9)
    np.testing.assert_allclose(m.data["Channel 2"].to_numpy(), np.concatenate([a[1], b[1]]), atol=1e-9)
    assert np.isnan(m.data["Channel 3"].to_numpy()[[10, 310]]).all()  # hors échelle resté vide


def test_gbd_overlap_and_gap_reported(tmp_path):
    make_gbd(tmp_path / "a.GBD", n=300, start="2026-10-29, 09:46:10")
    make_gbd(tmp_path / "b.GBD", n=300, start="2026-10-29, 09:46:10")  # même essai copié deux fois
    make_gbd(tmp_path / "c.GBD", n=300, start="2026-10-29, 10:30:00")  # 43 min plus tard
    result = merge_files([tmp_path / "c.GBD", tmp_path / "b.GBD", tmp_path / "a.GBD"], tmp_path / "f.csv")
    assert result.duplicates == 300 and result.rows == 600
    assert any("Trou" in n for n in result.notes)


def test_gbd_with_other_channels_or_units_blocks(tmp_path):
    make_gbd(tmp_path / "a.GBD", n=100)
    make_gbd(tmp_path / "b.GBD", n=100, start="2026-10-29, 10:00:00",
             amp={"CH1": "DC, 20V, Off", "CH2": "DC, 20V, Off", "CH3": "TEMP, TC_K, Off"})
    with pytest.raises(MergeError, match="b.GBD.*mêmes colonnes"):
        merge_files([tmp_path / "a.GBD", tmp_path / "b.GBD"], tmp_path / "f.csv")
    assert not (tmp_path / "f.csv").exists()


def test_gbd_and_csv_are_not_mixed(tmp_path):
    make_gbd(tmp_path / "a.GBD", n=100)
    graphtec(tmp_path / "b.CSV", "2026-10-29 10:00:00", 50)
    with pytest.raises(MergeError, match="a.GBD.*Graphtec .GBD.*b.CSV"):
        merge_files([tmp_path / "a.GBD", tmp_path / "b.CSV"], tmp_path / "f.csv")


def test_gbd_different_sampling_reported(tmp_path):
    make_gbd(tmp_path / "a.GBD", n=100, sample="100ms")
    make_gbd(tmp_path / "b.GBD", n=100, sample="1s", start="2026-10-29, 10:00:00")
    result = merge_files([tmp_path / "a.GBD", tmp_path / "b.GBD"], tmp_path / "f.csv")
    assert result.rows == 200
    assert any("Périodes d'échantillonnage différentes" in n and "100 ms" in n and "1000 ms" in n
               for n in result.notes)
