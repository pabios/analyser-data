"""Fusion chronologique de plusieurs fichiers CSV (ou Graphtec .GBD) en un seul (issue #19).

Règles validées par le client (commentaire de l'issue #19) :

* fichiers **compatibles** seulement : même ligne de titres (mêmes voies, même ordre), même
  séparateur, même décimale, même format de date ; sinon la fusion est **bloquée** avec un
  message qui nomme le fichier en cause ;
* lignes de mesure **recopiées telles quelles** (rien n'est recalculé ni reformaté), triées par
  date-heure croissante, quel que soit l'ordre ou le nom des fichiers ; en haut, le **bloc
  d'en-tête du fichier le plus ancien** et une seule ligne de titres ; encodage et fins de ligne
  du fichier le plus ancien ;
* ligne **strictement identique** (même date-heure, mêmes valeurs) : gardée une seule fois ;
* même date-heure mais valeurs différentes : les deux lignes gardées, dans l'ordre des fichiers,
  avec un avertissement ;
* heure locale sans fuseau : aucune conversion ; un recul de l'heure dans un fichier
  (changement d'heure ?) est signalé, pas corrigé ;
* trous et chevauchements entre fichiers : conservés et signalés ;
* format de date comparé sur l'ordre et les séparateurs, pas sur le nombre de chiffres (le
  Graphtec écrit « 2026/9/21 » puis « 2026/10/1 ») ;
* ligne **corrompue** isolée (date-heure illisible, ex. « USBCS°Œ » écrit par l'appareil) :
  retirée et signalée (fichier, numéro de ligne, contenu) ; trop de lignes illisibles : bloquée ;
* fichiers natifs **Graphtec .GBD** : décodés comme à l'import (mise à l'échelle de l'appareil
  comprise), la date-heure vient de l'heure de début de l'en-tête ; le fichier fusionné est un
  CSV (« ; », point décimal) relu comme un export Graphtec. GBD et CSV ne se mélangent pas.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

import numpy as np
import pandas as pd

from .excel import is_excel
from .gbd import GbdError, is_gbd, read_gbd
from .i18n import _
from .loader import (
    _INDEX_NAME_RE, _TO_CSV_EOL, LoadError, _extract_time, _hms, _merge_decimal_commas, _open_text, _thousands,
    _unwrap_quotes, analyze_text,
)

TEXT_SUFFIXES = {".csv", ".txt", ".dat", ".tsv", ".asc", ""}
MAX_CORRUPT_LINES = 20  # au-delà (et au-delà de MAX_CORRUPT_SHARE des lignes), la fusion est bloquée
MAX_CORRUPT_SHARE = 0.001
_SEP_NAMES = {";": "« ; »", ",": "« , »", "\t": "tabulation", "|": "« | »"}


class MergeError(Exception):
    """Fusion impossible (message destiné à l'utilisateur, nomme le fichier en cause)."""


@dataclass
class _Source:
    path: Path
    encoding: str
    bom: bool
    newline: str
    sep: str
    decimal: str
    headers: List[str]
    preamble: List[str]  # lignes avant les mesures, telles quelles (titres compris)
    lines: List[str]  # lignes de mesure, telles quelles
    times: np.ndarray  # date-heure de chaque ligne, en ns
    keys: List[str]  # contenu de la ligne sans les colonnes de numéro (doublons)
    date_shape: str  # ex. « 99/99/9999 99:99:99 »
    date_example: str
    ignored: List[str] = field(default_factory=list)
    backward: Optional[pd.Timestamp] = None  # premier recul de l'heure
    skipped: List[tuple] = field(default_factory=list)  # lignes corrompues retirées : (numéro, contenu)
    kind: str = "text"  # « text » ou « gbd »
    period: Optional[float] = None  # .GBD : période d'échantillonnage (s)
    model: str = ""  # .GBD : modèle de l'appareil


@dataclass
class MergeResult:
    path: Path
    files: List[Path]  # dans l'ordre chronologique
    rows: int
    duplicates: int
    start: pd.Timestamp
    end: pd.Timestamp
    notes: List[str]


def merge_files(paths: Sequence, out_path, progress: Optional[Callable[[str], None]] = None) -> MergeResult:
    """Fusionne ``paths`` dans ``out_path`` (voir les règles en tête de module)."""
    progress = progress or (lambda _text: None)
    paths = [Path(p) for p in paths]
    out_path = Path(out_path)
    if len(paths) < 2:
        raise MergeError(_("Choisissez au moins deux fichiers à fusionner."))
    if len({p.resolve() for p in paths}) < len(paths):
        raise MergeError(_("Le même fichier est choisi deux fois."))
    if out_path.resolve() in {p.resolve() for p in paths}:
        raise MergeError(_("Le fichier fusionné ne peut pas remplacer un des fichiers d'origine : "
                           "choisissez un autre nom."))

    sources = []
    for i, path in enumerate(paths, start=1):
        progress(_("Lecture du fichier {}/{} : {}…").format(i, len(paths), path.name))
        sources.append(_read_source(path))
    sources.sort(key=lambda s: s.times.min())  # ordre chronologique, quel que soit le choix
    ref = sources[0]
    for other in sources[1:]:
        _check_compatible(ref, other)
    if ref.kind == "gbd":
        ref.preamble = _gbd_preamble(ref, sources)

    progress(_("Tri chronologique de {} lignes…").format(_thousands(sum(len(s.lines) for s in sources))))
    times = np.concatenate([s.times for s in sources])
    order = np.argsort(times, kind="mergesort")  # stable : à égalité, ordre des fichiers puis des lignes
    lines = [line for s in sources for line in s.lines]
    keys = [key for s in sources for key in s.keys]
    table = pd.DataFrame({"t": times[order], "key": [keys[i] for i in order]})
    duplicate = table.duplicated(["t", "key"], keep="first").to_numpy()
    kept = order[~duplicate]
    kept_times = times[kept]

    notes = []
    duplicates = int(duplicate.sum())
    if duplicates:
        notes.append(_("{} ligne(s) en double (même date-heure, mêmes valeurs) gardée(s) une seule fois.")
                     .format(_thousands(duplicates)))
    same_time = pd.Series(kept_times).duplicated(keep=False).to_numpy()
    if same_time.any():
        first = pd.Timestamp(int(kept_times[same_time][0]))
        notes.append(_("{} ligne(s) ont la même date-heure qu'une autre mais des valeurs différentes : toutes "
                       "gardées, dans l'ordre des fichiers (premier instant : {}).").format(
                           _thousands(int(same_time.sum())), _stamp(first)))
    notes += _between_files(sources)
    for s in sources:
        if s.backward is not None:
            notes.append(_("« {} » : l'heure recule à {} (changement d'heure ?). Rien n'est corrigé : les lignes "
                           "sont rangées par date-heure.").format(s.path.name, _stamp(s.backward)))
        if s.skipped:
            number, content = s.skipped[0]
            notes.append(_("« {} » : {} ligne(s) corrompue(s) (date-heure illisible) retirée(s), ex. ligne {} : "
                           "« {} ».").format(s.path.name, len(s.skipped), number, content))
        if s.ignored:
            notes.append(_("« {} » : {} ligne(s) sans mesure après les titres non recopiée(s) (ex. « {} »).")
                         .format(s.path.name, len(s.ignored), s.ignored[0].strip()[:40]))
    if any(s.encoding != ref.encoding for s in sources):
        notes.append(_("Encodages différents selon les fichiers : fichier fusionné écrit en {} (celui de « {} »).")
                     .format(ref.encoding, ref.path.name))
    periods = {s.period for s in sources if s.period}
    if len(periods) > 1:
        notes.append(_("Périodes d'échantillonnage différentes selon les fichiers .GBD ({}) : lignes rangées par "
                       "date-heure, rien n'est rééchantillonné.").format(
                           ", ".join("{:g} ms".format(p * 1000) for p in sorted(periods))))
    if ref.kind == "gbd":
        notes.append(_("Fichiers Graphtec .GBD décodés comme à l'import (mise à l'échelle de l'appareil comprise) "
                       "et écrits en CSV."))
    elif len(ref.preamble) > 1:  # bloc d'en-tête d'appareil (Vendor, Model…) en plus des titres
        notes.append(_("En-tête recopié de « {} » (le plus ancien) : ses informations (nombre de points, début, "
                       "fin…) restent celles de ce fichier.").format(ref.path.name))

    progress(_("Écriture de {}…").format(out_path.name))
    encoding = ref.encoding
    if encoding == "utf-8" and ref.bom:
        encoding = "utf-8-sig"
    text = ref.newline.join(ref.preamble + [lines[i] for i in kept]) + ref.newline
    try:
        data = text.encode(encoding)
    except UnicodeEncodeError:
        data = text.encode("utf-8")
        notes.append(_("Caractères impossibles à écrire en {} : fichier fusionné écrit en UTF-8.").format(encoding))
    out_path.write_bytes(data)
    return MergeResult(out_path, [s.path for s in sources], len(kept), duplicates,
                       pd.Timestamp(int(kept_times.min())), pd.Timestamp(int(kept_times.max())), notes)


def merge_summary(result: MergeResult) -> str:
    """Résumé pour le journal des traitements."""
    return _("Fusion chronologique de {} fichiers ({}) dans {} : {} lignes, du {} au {}.").format(
        len(result.files), ", ".join(p.name for p in result.files), result.path.name, _thousands(result.rows),
        _stamp(result.start), _stamp(result.end))


# ---------------------------------------------------------------------------------------------

def _read_source(path: Path) -> _Source:
    if not path.is_file():
        raise MergeError(_("Fichier introuvable : {}").format(path))
    if is_gbd(path):
        return _read_gbd_source(path)
    if is_excel(path) or path.suffix.lower() not in TEXT_SUFFIXES:
        raise MergeError(_("« {} » : seuls les fichiers texte (CSV, TXT, DAT, TSV) et Graphtec .GBD peuvent être "
                           "fusionnés.").format(path.name))
    try:
        layout = analyze_text(path)
    except LoadError as exc:
        raise MergeError(_("« {} » est illisible :\n{}").format(path.name, exc)) from None
    if layout is None:
        raise MergeError(_("« {} » : colonnes alignées par des espaces, fusion non prise en charge.")
                         .format(path.name))

    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    newline = "\r\n" if b"\r\n" in raw[:65536] else "\n"
    with _open_text(path, layout.encoding) as fh:
        all_lines = fh.read().split("\n")
    while all_lines and not all_lines[-1].strip():
        all_lines.pop()
    preamble = all_lines[:layout.first_data]
    n_cols = len(layout.headers)

    lines, rows, numbers, ignored = [], [], [], []
    for number, line in enumerate(all_lines[layout.first_data:], start=layout.first_data + 1):
        if not line.strip():
            continue
        parsed = line
        if layout.unwrap:
            parsed = _unwrap_quotes([parsed])[0]
        if layout.merge_commas:
            parsed = _merge_decimal_commas([parsed])[0]
        fields = next(csv.reader([parsed], delimiter=layout.sep))
        if len([f for f in fields if f.strip()]) < 2:
            ignored.append(line)  # « End of data », commentaire…
            continue
        lines.append(line)
        rows.append((fields + [""] * n_cols)[:n_cols])
        numbers.append(number)
    if not rows:
        raise MergeError(_("« {} » ne contient aucune ligne de mesure.").format(path.name))

    frame = pd.DataFrame(rows, columns=range(n_cols), dtype=object)
    frame.columns = layout.headers
    try:
        seconds, start, used, _counter, _forced, _note = _extract_time(frame, layout.decimal, path.name)
    except LoadError as exc:
        raise MergeError(_("« {} » : {}").format(path.name, exc)) from None
    if start is None:
        raise MergeError(_("« {} » : la colonne temps ne donne pas de date-heure (temps écoulé seulement) : "
                           "impossible de ranger les fichiers dans l'ordre chronologique.\nPour des essais Graphtec, "
                           "fusionnez plutôt les fichiers .GBD d'origine : ils contiennent l'heure de début.")
                         .format(path.name))
    seconds = np.asarray(seconds, dtype=float)
    bad = np.flatnonzero(~np.isfinite(seconds))
    if len(bad) > max(MAX_CORRUPT_LINES, len(seconds) * MAX_CORRUPT_SHARE) or len(bad) == len(seconds):
        k = int(bad[0])  # trop de lignes illisibles : format de date mal compris, pas une ligne abîmée
        raise MergeError(_("« {} », ligne {} : date-heure illisible : fusion impossible.\n« {} »").format(
            path.name, numbers[k], lines[k].strip()[:80]))
    skipped = [(numbers[int(k)], lines[int(k)].strip()[:80]) for k in bad]
    if len(bad):
        good = np.isfinite(seconds)
        keep = np.flatnonzero(good)
        lines = [lines[i] for i in keep]
        rows = [rows[i] for i in keep]
        frame = frame.iloc[keep].reset_index(drop=True)
        seconds = seconds[good]
    times = start.value + np.round(seconds * 1e9).astype(np.int64)
    backward = None
    steps = np.diff(seconds)
    if (steps < 0).any():
        backward = pd.Timestamp(int(times[int(np.flatnonzero(steps < 0)[0]) + 1]))

    time_cols = [c for c in layout.headers if c in used]
    first_time = " ".join(str(frame[c].iloc[0]).strip() for c in time_cols)
    index_cols = [i for i, h in enumerate(layout.headers) if _INDEX_NAME_RE.match(h.strip())]
    keys = ["\x1f".join(f.strip() for i, f in enumerate(fields) if i not in index_cols) for fields in rows]
    return _Source(path, layout.encoding, bom, newline, layout.sep, layout.decimal, list(layout.headers),
                   preamble, lines, times, keys, date_shape(first_time), first_time, ignored, backward, skipped)


def date_shape(text: str) -> str:
    """Forme d'une date-heure : ordre des champs et séparateurs, sans tenir compte des zéros non
    écrits (« 2026/9/21 16:47:49 » et « 2026/10/1 09:21:19 » ont la même forme)."""
    return re.sub(r"\d+", lambda m: "9999" if len(m.group()) == 4 else "9", text.strip())


def _read_gbd_source(path: Path) -> _Source:
    """Fichier natif Graphtec .GBD : décodé comme à l'import, une ligne CSV par mesure."""
    try:
        frame, info = read_gbd(path)
    except (GbdError, OSError, ValueError) as exc:
        raise MergeError(_("« {} » est illisible :\n{}").format(path.name, exc)) from None
    headers = ["Date&Time"] + ["{} [{}]".format(c, u) if u else c for c, u in zip(frame.columns[1:], info["units"])]
    text = frame.to_csv(sep=";", index=False, header=False, date_format="%Y-%m-%d %H:%M:%S.%f",
                        float_format="%.10g", **{_TO_CSV_EOL: "\n"})
    lines = text.split("\n")[:-1]
    times = pd.DatetimeIndex(frame["Date&Time"]).asi8.astype(np.int64)
    backward = None
    if (np.diff(times) < 0).any():
        backward = pd.Timestamp(int(times[int(np.flatnonzero(np.diff(times) < 0)[0]) + 1]))
    return _Source(path, "utf-8", False, "\r\n", ";", ".", headers, [";".join(headers)], lines, times, lines,
                   "gbd", lines[0].split(";")[0] if lines else "", backward=backward, kind="gbd",
                   period=info["period"], model=info["model"])


def _gbd_preamble(ref: _Source, sources: List[_Source]) -> List[str]:
    """En-tête du CSV fusionné à partir de .GBD : relu comme un export Graphtec."""
    return ["Vendor;GRAPHTEC Corporation", "Model;{}".format(ref.model), "Sampling;{:g}ms".format(ref.period * 1000),
            "Total data points;{}".format(sum(len(s.lines) for s in sources)), "Data", ";".join(ref.headers)]


def _check_compatible(ref: _Source, other: _Source) -> None:
    name = other.path.name
    if other.kind != ref.kind:
        gbd, text = (other, ref) if other.kind == "gbd" else (ref, other)
        raise MergeError(_("« {} » est un fichier Graphtec .GBD et « {} » un fichier texte : fusionnez soit "
                           "uniquement des .GBD, soit uniquement des CSV.").format(gbd.path.name, text.path.name))
    if other.sep != ref.sep:
        raise MergeError(_("« {} » n'a pas le même séparateur que « {} » ({} au lieu de {}) : fusion impossible.")
                         .format(name, ref.path.name, _(_SEP_NAMES.get(other.sep, other.sep)),
                                 _(_SEP_NAMES.get(ref.sep, ref.sep))))
    if other.decimal != ref.decimal:
        raise MergeError(_("« {} » n'a pas la même décimale que « {} » (« {} » au lieu de « {} ») : fusion "
                           "impossible.").format(name, ref.path.name, other.decimal, ref.decimal))
    a = [h.strip() for h in ref.headers]
    b = [h.strip() for h in other.headers]
    if a != b:
        missing = [h for h in a if h not in b]
        extra = [h for h in b if h not in a]
        if missing:
            detail = _("voie(s) absente(s) : {}").format(", ".join(missing))
        elif extra:
            detail = _("voie(s) en plus : {}").format(", ".join(extra))
        else:
            detail = _("voies dans un autre ordre")
        raise MergeError(_("« {} » n'a pas les mêmes colonnes que « {} » ({}) : fusion impossible.")
                         .format(name, ref.path.name, detail))
    if other.date_shape != ref.date_shape:
        raise MergeError(_("« {} » n'a pas le même format de date que « {} » (« {} » au lieu de « {} ») : "
                           "fusion impossible.").format(name, ref.path.name, other.date_example, ref.date_example))


def _between_files(sources: List[_Source]) -> List[str]:
    notes = []
    for prev, nxt in zip(sources, sources[1:]):
        end, start = int(prev.times.max()), int(nxt.times.min())
        steps = np.diff(np.sort(prev.times))
        period = float(np.median(steps)) if len(steps) else 0.0
        gap = (start - end) / 1e9
        if start < end:
            known = set(zip(prev.times.tolist(), prev.keys))
            inside = nxt.times <= end
            new = sum(1 for t, k in zip(nxt.times[inside].tolist(), np.asarray(nxt.keys, dtype=object)[inside])
                      if (t, k) not in known)
            if new:
                notes.append(_("« {} » et « {} » se chevauchent sur {} : leurs lignes sont mélangées dans l'ordre "
                               "chronologique.").format(prev.path.name, nxt.path.name, _hms((end - start) / 1e9)))
            else:
                notes.append(_("« {} » reprend la fin de « {} » ({}) avec les mêmes lignes : gardées une seule fois.")
                             .format(nxt.path.name, prev.path.name, _hms((end - start) / 1e9)))
        elif period > 0 and gap > 2 * period / 1e9:
            notes.append(_("Trou de {} entre « {} » (fin {}) et « {} » (début {}) : conservé, aucune valeur "
                           "ajoutée.").format(_hms(gap), prev.path.name, _stamp(pd.Timestamp(end)), nxt.path.name,
                                              _stamp(pd.Timestamp(start))))
    return notes


def _stamp(moment: pd.Timestamp) -> str:
    text = moment.strftime("%d/%m/%Y %H:%M:%S")
    if moment.microsecond:
        text += ",{:03d}".format(moment.microsecond // 1000)
    return text
