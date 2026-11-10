"""US-01 — Import et normalisation des fichiers de mesure multi-formats.

Chaque centrale d'acquisition (Graphtec, Nanodac, Graphset, enceinte climatique...)
produit des fichiers CSV/TXT/DAT différents. Ce module détecte automatiquement :

* l'encodage (utf-8, cp1252, latin-1) ;
* le séparateur (";", tabulation, ",") et le séparateur décimal ;
* la ligne d'en-tête (en ignorant le préambule éventuel de la centrale) ;
* la colonne temps (date/heure absolue, durée H:MM:SS, ou temps écoulé en ms / s / min) ;
* la période d'échantillonnage.

Tous les fichiers sont ensuite ramenés sur un axe temps commun ``Time_min`` (en minutes),
calculé par rapport au fichier de référence (Graphset, sinon le fichier le plus long).
"""
from __future__ import annotations

import csv
import inspect
import io
import math
import os
import re
import tempfile
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .channels import (
    CURRENT, DEFAULT_CHANNEL_UNIT, OTHER, VOLTAGE, Channel, make_channel, normalize_unit, quantity_of,
    split_name_unit,
)
from .excel import ExcelError, format_cell, is_excel, read_rows, sheet_names
from .gbd import GbdError, is_gbd, read_gbd
from .gbd import read_header as read_gbd_header
from .i18n import _

TIME_COL = "Time_min"

ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
SEPARATORS = ("\t", ";", "|", ",")  # par ordre de priorité ("," peut être un décimal) ; « | » : issue #16
SPACE = " "  # colonnes séparées par des espaces (choix manuel, ou en dernier recours)
# Formats acceptés (issue #10) ; d'autres (PDF, Word…) sont refusés avec un message clair
TEXT_SUFFIXES = {".csv", ".txt", ".dat", ".tsv", ".asc", ""}
UNSUPPORTED_SUFFIXES = {".pdf", ".doc", ".docx", ".odt", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".zip",
                        ".rar", ".7z", ".exe", ".ods", ".mat", ".tdms"}
# Données géographiques (SIG) : cartes, pas des mesures dans le temps (issue #16)
GIS_SUFFIXES = {".gdb", ".shp", ".geojson", ".gpkg", ".kml", ".kmz"}
TABLE_SUFFIXES = {".json", ".parquet"}  # tableaux structurés, lus par pandas (issue #16)
SEPARATOR_NAMES = {"\t": "tabulation", ";": "point-virgule", ",": "virgule", "|": "barre verticale"}

_TIME_NAME_RE = re.compile(
    r"(time|temps|date|heure|hour|elapsed|dur[ée]e|horodat|timestamp|^t$|datum|uhrzeit|zeit|fecha|hora|tiempo)",
    re.IGNORECASE
)
_DATE_ONLY_NAME_RE = re.compile(r"^(date|datum|fecha)$", re.IGNORECASE)
_SUBSECOND_NAME_RE = re.compile(r"^(ms|msec|us|µs)$", re.IGNORECASE)
# Pas de groupe capturant : motifs passés à pandas (RE2 sous pyarrow)
_MIDNIGHT_SUFFIX_RE = r"\s+00:00(?::00(?:\.0+)?)?$"
_ISO_DAY_RE = r"\d{4}-\d{2}-\d{2}\s"
_SUBSECOND_FACTOR = {"ms": 1e-3, "msec": 1e-3, "us": 1e-6, "µs": 1e-6}
# Valeurs hors échelle écrites par les centrales à la place d'un nombre.
# Attention : les motifs passés aux méthodes .str de pandas doivent rester compatibles
# avec RE2 (moteur utilisé quand pyarrow est installé, comme sous Anaconda) : pas de
# référence arrière (\1) dans un motif.
_OVER_RANGE_PATTERN = r"^(\+{3,}|-{3,}|(?i:burnout|over|under|overrange|underrange|-?ovf|err))$"
_OVER_RANGE_RE = re.compile(_OVER_RANGE_PATTERN)
_INDEX_NAME_RE = re.compile(r"^(no\.?|n°|num(ber|[ée]ro)?|index|#|id|ligne)$", re.IGNORECASE)
# Noms de compteurs d'échantillons (jamais des temps, même s'ils contiennent « time ») — issue #15
_COUNTER_NAME_RE = re.compile(r"sample|[ée]chantillon|point|count|compteur|\bpt\b|\bnr\b|data ?no", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^[+-]?\s*(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")  # « + 24.003 » accepté
_DECIMAL_COMMA_RE = re.compile(r"^[+-]?\s*\d*,\d+([eE][+-]?\d+)?$")
# Espaces à l'intérieur d'un nombre : « 0 ,01 » (issue #2, X_axis_D2.CSV), séparateur de
# milliers « 1 234,5 », espaces insécables (\u00a0, \u202f) d'un Windows / Excel français.
_INNER_SPACES = "[\\s\u00a0\u202f\u2009]"
_INNER_SPACES_RE = re.compile(_INNER_SPACES)


def _compact(field_: str) -> str:
    """Cellule sans guillemets ni espaces (y compris insécables) : « "  0 ,01" » -> « 0,01 »."""
    return _INNER_SPACES_RE.sub("", field_.strip().strip('"').lstrip("'"))
# Séparateur « , » ET décimale « , » sans guillemets : « …,+24,003,-1,5,… ». Les valeurs
# Graphtec portent toujours un signe : « +24 » suivi de « 003 » est un seul nombre.
_SIGNED_INT_RE = re.compile(r"^[+-]\s*\d+$")
_DIGITS_RE = re.compile(r"^\d+([eE][+-]?\d+)?$")
_POINT_DECIMAL_RE = re.compile(r"^[+-]?\s*\d+\.\d+")
_MERGE_COMMA_RE = re.compile(r"(^|,)([+-] *\d+),(\d+(?:[eE][+-]?\d+)?)(?=,|$)", re.MULTILINE)
_WHOLE_QUOTED_RE = re.compile(r'^"(.*)"$', re.MULTILINE)
_SAMPLING_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(us|µs|ms|msec|s|sec|min|h)\s*$", re.IGNORECASE)
_DATETIME_LIKE_RE = re.compile(r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}|^\d{1,3}:\d{2}")
_DURATION_RE = re.compile(r"^\d+:\d{2}:\d{2}([.,]\d+)?$")
_COMMA_SECONDS_RE = re.compile(r":\d{2},\d+$")
_YEAR_FIRST_RE = re.compile(r"^\d{4}[/\-.]")
# Unités proposées dans la fenêtre Import pour une colonne temps sans unité écrite (issue #18)
TIME_UNIT_NAMES = {"ms": "millisecondes", "s": "secondes", "min": "minutes"}
_TIME_UNITS_S = {"ms": 1e-3, "msec": 1e-3, "s": 1.0, "sec": 1.0, "min": 60.0, "h": 3600.0}

_SOURCES = (
    ("graphset", "Graphset"),
    ("graphtec", "Graphtec"),
    ("nanodac", "Nanodac"),
    ("clim", "Clim"),
    ("enceinte", "Clim"),
)


class LoadError(Exception):
    """Fichier illisible ou non conforme. Le message est destiné à l'utilisateur."""


@dataclass
class Measurement:
    """Un fichier de mesure chargé et normalisé."""

    name: str  # nom affiché (nom du fichier, rendu unique)
    path: Path
    source: str  # type de centrale détecté : Graphtec, Nanodac, Graphset, Clim, Générique
    encoding: str
    separator: str
    decimal: str
    channels: List[Channel]
    data: pd.DataFrame  # colonne Time_min + une colonne par voie (libellés nettoyés)
    elapsed_min: np.ndarray  # temps écoulé depuis le début du fichier (minutes)
    period_s: float  # période d'échantillonnage détectée
    start: Optional[pd.Timestamp] = None  # horodatage absolu du 1er point, si disponible
    align_offset_min: float = 0.0  # décalage appliqué pour l'axe commun
    align_note: str = ""  # explication si le fichier n'a pas pu être recalé sur la référence
    warnings: List[str] = field(default_factory=list)
    sheet: str = ""  # feuille Excel lue (classeurs .xlsx / .xls)
    time_unit: str = ""  # unité du temps imposée à l'import (« ms », « s », « min »), issue #18

    @property
    def duration_min(self) -> float:
        return float(self.elapsed_min[-1]) if len(self.elapsed_min) else 0.0

    @property
    def is_thermal(self) -> bool:
        """Fichier d'enceinte climatique (ex. nanodac) : des voies °C / %HR et aucune voie
        électrique (V, A). Les autres colonnes (consigne, sortie de régulation en %, état de
        porte, unité inconnue…) ne l'empêchent pas (issue #13).

        Ses courbes suivent le curseur de décalage temporel (retard du banc thermique).
        """
        return any(ch.is_climatic for ch in self.channels) and not any(
            ch.quantity in (VOLTAGE, CURRENT) for ch in self.channels)

    @property
    def period_label(self) -> str:
        return format_period(self.period_s)

    def channel(self, label: str) -> Channel:
        for ch in self.channels:
            if ch.label == label:
                return ch
        raise KeyError(label)


# --------------------------------------------------------------------------- API


def load_measurement(
    path, name: Optional[str] = None, progress: Optional[Callable[[str], None]] = None,
    sep: Optional[str] = None, sheet: Optional[str] = None, time_unit: Optional[str] = None,
) -> Measurement:
    """Charge un fichier de mesure (texte ou classeur Excel). Lève :class:`LoadError` si le
    fichier est non conforme.

    ``progress(message)`` est appelé à chaque étape (lecture, conversion...).
    ``sep`` : séparateur imposé (« ; », « , », tabulation, espace) au lieu de la détection.
    ``sheet`` : feuille d'un classeur Excel (par défaut : la première).
    ``time_unit`` : unité d'une colonne temps en nombres sans unité écrite (« ms », « s », « min »),
    au lieu des secondes supposées (issue #18 : export CSV SEFRAM en ms). Sans effet sur une date/heure.
    """
    path = Path(path)
    name = name or path.name
    progress = progress or (lambda _msg: None)
    try:
        suffix = path.suffix.lower()
        if suffix in GIS_SUFFIXES or path.is_dir() and suffix == ".gdb":
            raise LoadError(_("« {} » est un fichier de données géographiques (SIG : carte, couches, coordonnées), "
                              "pas un enregistrement de mesures dans le temps : il n'est pas pris en charge. Pour "
                              "un fichier Graphtec natif (.GBD), exportez-le en CSV depuis l'appareil ou son "
                              "logiciel.").format(path.name))
        if is_gbd(path):
            return _load_gbd(path, name, progress)
        if suffix in TABLE_SUFFIXES:
            return _load_via_csv(path, name, progress, _table_rows(path, progress), "", time_unit)
        if suffix in UNSUPPORTED_SUFFIXES:
            raise LoadError(_("Le format « {} » du fichier « {} » n'est pas pris en charge. Formats acceptés : "
                              "CSV, TXT, DAT, TSV (texte), Excel XLSX, XLS, JSON et Parquet.").format(suffix, path.name))
        if is_excel(path):
            return _load_excel(path, name, progress, sheet, time_unit)
        if sep == SPACE:
            return _load_via_csv(path, name, progress, _space_rows(path), "", time_unit)
        return _load(path, name, progress, sep, time_unit)
    except LoadError:
        raise
    except ExcelError as exc:
        raise LoadError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - filet de sécurité
        raise LoadError(
            _("Le fichier « {} » n'a pas pu être lu : {}").format(path.name, exc)
        ) from exc


def choose_reference(measurements: Sequence[Measurement]) -> Optional[Measurement]:
    """Fichier de référence : le Graphset s'il existe, sinon le fichier le plus long.

    Parmi les fichiers couvrant au moins la moitié de la durée maximale, on prend le plus finement
    échantillonné : l'export se fait sur sa grille de temps, il ne faut pas perdre le
    détail d'un enregistreur rapide (Graphtec 100 ms) au profit d'un lent (nanodac 1 min).
    """
    if not measurements:
        return None
    for m in measurements:
        if m.source == "Graphset":
            return m
    longest = max(m.duration_min for m in measurements)
    candidates = [m for m in measurements if m.duration_min >= 0.5 * longest]
    return min(candidates, key=lambda m: (m.period_s if math.isfinite(m.period_s) else math.inf, -m.duration_min))


def align_time_axes(measurements: Sequence[Measurement]) -> Optional[Measurement]:
    """Recalcule ``Time_min`` de chaque fichier par rapport au fichier de référence.

    Si les fichiers sont horodatés, leurs débuts sont recalés sur celui de la référence ;
    sinon chaque fichier démarre à 0.
    Un fichier qui ne recouvre pas du tout la période de la référence vient sans doute
    d'un autre essai : il démarre alors aussi à 0 (pour comparer les courbes) et
    ``align_note`` l'explique.
    """
    ref = choose_reference(measurements)
    for m in measurements:
        offset = 0.0
        m.align_note = ""
        if ref is not None and m.start is not None and ref.start is not None:
            offset = (m.start - ref.start).total_seconds() / 60.0
            overlaps = offset <= ref.duration_min and offset + m.duration_min >= 0
            if not overlaps:
                m.align_note = (
                    _("« {} » ({}) ne couvre pas la même période que la référence « {} » ({}) : "
                    "autre essai ? Il est affiché à partir de 0 pour comparer les courbes.").format(
                        m.name, m.start.strftime("%d/%m/%Y %H:%M"), ref.name, ref.start.strftime("%d/%m/%Y %H:%M"))
                )
                offset = 0.0
        m.align_offset_min = offset
        m.data[TIME_COL] = m.elapsed_min + offset
    return ref


def format_period(seconds: float) -> str:
    """Période lisible : "500 ms", "1 s", "10 s", "1 min"."""
    if not seconds or not math.isfinite(seconds):
        return "?"
    if seconds < 1:
        return _("{:g} ms").format(round(seconds * 1000, 3))
    if seconds < 60:
        return _("{:g} s").format(round(seconds, 3))
    return _("{:g} min").format(round(seconds / 60, 3))


# ------------------------------------------------------------------ implémentation


HEAD_BYTES = 2 * 1024 * 1024  # début du fichier analysé (préambule + premières mesures)
TAIL_BYTES = 256 * 1024  # fin du fichier analysée (séparateur, nombre de colonnes)


def _load_excel(path: Path, name: str, progress: Callable[[str], None], sheet: Optional[str],
                time_unit: Optional[str] = None) -> Measurement:
    """Classeur Excel : la feuille est écrite en CSV temporaire, puis lue comme un fichier texte."""
    names = sheet_names(path)
    sheet = sheet or (names[0] if names else None)
    rows = ([format_cell(v) for v in row] for row in read_rows(path, sheet, progress))
    m = _load_via_csv(path, name, progress, rows, sheet, time_unit)
    if len(names) > 1:
        m.warnings.insert(0, _("Feuille « {} » ({} feuilles dans le classeur).").format(sheet, len(names)))
    return m


def _load_via_csv(path: Path, name: str, progress, rows, sheet: str, time_unit: Optional[str] = None) -> Measurement:
    """Lignes déjà découpées (Excel, colonnes séparées par des espaces) -> CSV « ; » temporaire."""
    fd, temp = tempfile.mkstemp(suffix=".csv", prefix="analyser_data_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, delimiter=";", lineterminator="\n")
            count = 0
            for row in rows:
                while row and row[-1] == "":
                    row = row[:-1]
                writer.writerow(row)
                count += 1
        if not count:
            raise LoadError(_("Le fichier « {} » est vide.").format(path.name))
        try:
            m = _load(Path(temp), name, progress, ";", time_unit)
        except LoadError as exc:  # messages : nom du fichier d'origine, pas du CSV temporaire
            raise LoadError(str(exc).replace(Path(temp).name, path.name)) from None
    finally:
        try:
            os.remove(temp)
        except OSError:
            pass
    m.path, m.sheet = path, sheet or ""
    if sheet is not None and not sheet:
        m.separator = SPACE
    return m


# pandas < 1.5 (Anaconda plus ancien, issue #16) : « line_terminator » au lieu de « lineterminator »
_TO_CSV_EOL = "lineterminator" if "lineterminator" in inspect.signature(pd.DataFrame.to_csv).parameters \
    else "line_terminator"


def _load_gbd(path: Path, name: str, progress) -> Measurement:
    """Fichier natif Graphtec .GBD (issue #16) : décodé, écrit en CSV temporaire, puis lu comme
    un export CSV de l'appareil (voies « Channel N », unités, période, contrôles)."""
    progress(_("Décodage du fichier Graphtec {}…").format(path.name))
    try:
        frame, info = read_gbd(path)
    except GbdError as exc:
        raise LoadError(str(exc)) from exc
    fd, temp = tempfile.mkstemp(suffix=".csv", prefix="analyser_data_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write("Vendor;GRAPHTEC Corporation\nModel;{}\nSampling;{:g}ms\nTotal data points;{}\nData\n".format(
                info["model"], info["period"] * 1000, info["counts"]))
            header = ["Date&Time"] + ["{} [{}]".format(c, u) if u else c for c, u in zip(frame.columns[1:], info["units"])]
            frame.to_csv(fh, sep=";", index=False, header=header, date_format="%Y-%m-%d %H:%M:%S.%f",
                         float_format="%.10g", **{_TO_CSV_EOL: "\n"})
        try:
            m = _load(Path(temp), name, progress, ";")
        except LoadError as exc:
            raise LoadError(str(exc).replace(Path(temp).name, path.name)) from None
    finally:
        try:
            os.remove(temp)
        except OSError:
            pass
    m.path, m.source, m.separator = path, "Graphtec", "GBD"
    m.warnings[:0] = [_("Lu directement depuis le fichier natif Graphtec .GBD (lecture à vérifier une première "
                        "fois avec l'export CSV de l'appareil).")] + info["notes"]
    return m


def _table_rows(path: Path, progress):
    """Fichier JSON (tableau d'objets ou de colonnes) ou Parquet -> en-têtes + lignes de texte."""
    progress(_("Lecture de {}…").format(path.name))
    try:
        if path.suffix.lower() == ".parquet":
            table = pd.read_parquet(path)
        else:
            try:
                table = pd.read_json(path)
            except ValueError:
                table = pd.read_json(path, lines=True)  # un objet JSON par ligne
    except ImportError as exc:
        raise LoadError(_("Lecture de « {} » impossible : module manquant ({}).").format(path.name, exc)) from exc
    except (ValueError, OSError) as exc:
        raise LoadError(_("Le fichier « {} » n'est pas un tableau lisible : {}").format(path.name, exc)) from exc
    if not isinstance(table, pd.DataFrame) or table.empty:
        raise LoadError(_("Le fichier « {} » est vide.").format(path.name))
    yield [str(c) for c in table.columns]
    for row in table.itertuples(index=False):
        yield [format_cell(v.to_pydatetime() if isinstance(v, pd.Timestamp) else
                           (None if v is None or (isinstance(v, float) and v != v) else v)) for v in row]


def _space_rows(path: Path):
    """Colonnes séparées par un ou plusieurs espaces (fichiers alignés en colonnes)."""
    encoding = _detect_encoding(path)
    with _open_text(path, encoding) as fh:
        for line in fh:
            if line.strip():
                yield line.split()


def _load(path: Path, name: str, progress: Callable[[str], None], forced_sep: Optional[str] = None,
          time_unit: Optional[str] = None) -> Measurement:
    if not path.is_file():
        raise LoadError(_("Fichier introuvable : {}").format(path))
    size_mb = path.stat().st_size / 1e6
    progress(_("Analyse du format de {} ({:.0f} Mo)…").format(path.name, size_mb))

    encoding = _detect_encoding(path)
    head, tail = _head_tail_lines(path, encoding)
    if not any(line.strip() for line in head):
        raise LoadError(_("Le fichier « {} » est vide.").format(path.name))

    sample = [line for line in head[-300:] + tail[-300:] if line.strip()]
    unwrap = _lines_fully_quoted(sample)
    if unwrap:
        head, tail, sample = (_unwrap_quotes(x) for x in (head, tail, sample))
    # Les dernières lignes du fichier sont toujours des mesures : sur un enregistrement court,
    # le préambule (30 lignes « AMP settings » d'un GL860) peut être plus long que les données
    # et fausser la détection (issues #8, #9).
    sep, last = _trailing_data_block([line for line in tail if line.strip()][-300:],
                                     (forced_sep,) if forced_sep else SEPARATORS)
    if sep is None:
        if forced_sep:
            sep, last = forced_sep, sample[-50:]
        else:
            try:
                sep = _detect_separator(sample, path.name, head)
            except LoadError:
                if _looks_space_separated(sample):  # dernier recours : colonnes alignées par des espaces
                    return _load_via_csv(path, name, progress, _space_rows(path), "")
                raise
            last = sample[-50:]
    merge_commas = sep == "," and _needs_comma_merge(list(csv.reader(sample, delimiter=sep)))
    if merge_commas:
        head, tail, sample, last = (_merge_decimal_commas(x) for x in (head, tail, sample, last))
    head_rows = list(csv.reader(head, delimiter=sep))
    sample_rows = list(csv.reader(sample, delimiter=sep))
    n_cols = _modal_field_count(list(csv.reader(last, delimiter=sep)))
    decimal = "," if merge_commas else _detect_decimal(sample_rows, sep, n_cols)
    # Colonnes qui contiennent des valeurs dans les vraies lignes de mesure (fin du fichier)
    signature = _value_signature(sample_rows[-50:], n_cols)

    first_data = _find_first_data_row(head_rows, n_cols, signature)
    if first_data is None:
        raise LoadError(
            _("Le fichier « {} » ne contient pas de données numériques exploitables "
            "(aucune ligne de mesure reconnue).\n\n{}").format(
                path.name, _diagnostic(head, sep, n_cols, encoding))
        )
    headers, units, aliases = _find_headers(head_rows, first_data, n_cols)
    signal_names = _graphtec_amp_settings(head_rows[:first_data])
    source = _guess_source(path.name, head[:first_data])
    default_unit = DEFAULT_CHANNEL_UNIT if source == "Graphtec" else ""
    for col, (_alias, unit) in signal_names.items():
        units.setdefault(col, unit)

    progress(_("Lecture des mesures de {} ({:.0f} Mo)…").format(path.name, size_mb))
    raw = _read_table(path, encoding, sep, decimal, first_data, headers, unwrap, merge_commas)

    # Lignes de fin presque vides (« End of data », « Comment,… ») : ce ne sont pas des mesures
    if raw.shape[1] > 3:
        raw = raw[raw.notna().sum(axis=1) >= 3].reset_index(drop=True)

    progress(_("Conversion du temps de {}…").format(path.name))
    time_s, start, used_cols, counter, unit_forced, unit_note = _extract_time(raw, decimal, path.name, time_unit)
    period_declared = _declared_sampling(head_rows[:first_data])
    counter_note = ""
    if counter:
        # Issue #15 : compteur d'échantillons (1, 2, 3…) pris pour des secondes -> un essai de 3 h
        # à 100 ms s'affichait sur 30 h. Le temps vient de la période annoncée dans l'en-tête.
        if period_declared > 0:
            time_s = (time_s - time_s[np.isfinite(time_s)][0]) * period_declared
            counter_note = _("La colonne « {} » est un compteur d'échantillons, pas un temps : temps calculé avec "
                             "la période annoncée dans l'en-tête ({}).").format(counter, format_period(period_declared))
        else:
            counter_note = _("La colonne « {} » semble être un compteur d'échantillons : 1 point par seconde supposé. "
                             "Si l'appareil enregistre plus vite, la durée affichée est fausse : exportez le fichier "
                             "avec sa colonne date / heure ou avec la période d'échantillonnage.").format(counter)
    time_s, rebuilt = _rebuild_coarse_time(time_s, period_declared)
    # Issue #15 : temps lu incohérent avec l'en-tête (période × nombre de points, début / fin) :
    # l'en-tête de l'enregistreur fait foi (ex. durée ×10)
    scale_note = ""
    if not unit_forced:  # unité choisie par l'utilisateur : elle fait foi
        time_s, scale_note = _check_against_header(time_s, head_rows[:first_data], period_declared)

    # Voies de mesure : toutes les autres colonnes numériques
    numeric_cols = []
    rejected = []  # (colonne, exemple de valeur) : pour le message d'erreur
    over_range = 0
    for col in raw.columns:
        if col in used_cols or _INDEX_NAME_RE.match(col.strip()) or not col.strip():
            continue
        column = raw[col]
        if not pd.api.types.is_numeric_dtype(column):
            if _to_numeric(_spread_sample(column), decimal).notna().sum() == 0:
                filled = column.dropna().astype(str)
                rejected.append((col, filled.iloc[0] if len(filled) else ""))
                continue  # colonne de texte (alarmes, messages...) : inutile de tout convertir
            text = column.dropna().astype(str).str.strip()
            over_range += int(text.str.match(_OVER_RANGE_PATTERN).sum())
            column = _to_numeric(column, decimal)
        if column.notna().sum() == 0:
            continue  # colonne de texte (alarmes, messages...)
        numeric_cols.append((col, column))

    # Graphtec dont l'en-tête des données ne porte pas les noms « CH3, CH4… » (unités,
    # numéros…) : les voies du tableau AMP settings sont rattachées dans l'ordre.
    by_order = {}
    if (signal_names and len(signal_names) == len(numeric_cols)
            and not any(col in signal_names for col, _column in numeric_cols)):
        by_order = {col: ch for (col, _column), ch in zip(numeric_cols, signal_names)}

    channels: List[Channel] = []
    values = {}
    for col, column in numeric_cols:
        ch_name = by_order.get(col, col)
        alias = signal_names.get(ch_name, ("", ""))[0] or aliases.get(col, "")
        unit = signal_names[ch_name][1] if col in by_order else units.get(col)
        ch = make_channel(ch_name, unit, alias=alias, default_unit=default_unit)
        label = _unique(ch.label, values)
        if label != ch.label:
            ch = Channel(ch.raw_name, label, ch.unit, ch.quantity, ch.alias)
        channels.append(ch)
        values[label] = column.to_numpy(dtype=float)

    if not channels:
        raise LoadError(
            _("Le fichier « {} » ne contient aucune voie de mesure numérique "
            "en plus de la colonne temps.\n\nColonnes lues : {}").format(path.name, ", ".join(headers))
            + _("\n\nValeurs lues (première ligne de mesure n° {}) : {}").format(
                first_data + 1, ", ".join("{} = « {} »".format(c, v[:20]) for c, v in rejected[:5]))
        )

    df = pd.DataFrame(values)
    df.insert(0, "_t", time_s)
    valid = np.isfinite(df["_t"].to_numpy())
    skipped = int((~valid).sum())
    df = df[valid]
    if df.empty:
        raise LoadError(_("La colonne temps du fichier « {} » est illisible.").format(path.name))

    msgs = [unit_note] if unit_note else []
    declared = _declared_count(head_rows[:first_data])
    if declared and declared > len(df) * 1.001 + 1:
        msgs.append(
            _("Le fichier annonce {} points mais n'en contient que {} : il est incomplet{}. "
            "Utilisez de préférence le fichier d'origine de l'appareil.").format(
                _thousands(declared), _thousands(len(df)),
                _(" (probablement tronqué par Excel, limité à 1 048 576 lignes)")
                if len(df) + first_data >= EXCEL_MAX_ROWS - 50 or declared > EXCEL_MAX_ROWS else "")
        )
    if counter_note:
        msgs.append(counter_note)
    if scale_note:
        msgs.append(scale_note)
    if rebuilt:
        msgs.append(_("Horodatage moins précis que la période d'échantillonnage ({}) : temps "
                    "recalculé à partir de la période (fichier réenregistré par Excel ?).").format(
                        format_period(rebuilt)))
    if skipped:
        msgs.append(_("{} ligne(s) sans horodatage ignorée(s) (messages, lignes incomplètes).").format(skipped))
    if over_range:
        msgs.append(_("{} valeur(s) hors échelle (+++++++, BURNOUT...) laissées vides : "
                    "« Nettoyer » répare les plus courtes.").format(over_range))
    if not df["_t"].is_monotonic_increasing:
        df = df.sort_values("_t", kind="mergesort")
        msgs.append(_("Points remis dans l'ordre chronologique."))
    df = df.reset_index(drop=True)
    msgs += _gap_notes(df["_t"].to_numpy(dtype=float), start)

    elapsed_min = (df.pop("_t").to_numpy(dtype=float)) / 60.0
    df.insert(0, TIME_COL, elapsed_min.copy())
    period_s = _sampling_period(elapsed_min * 60.0)

    return Measurement(
        name=name,
        path=path,
        source=source,
        encoding=encoding,
        separator=sep,
        decimal=decimal,
        channels=channels,
        data=df,
        elapsed_min=elapsed_min,
        period_s=period_s,
        start=start,
        warnings=msgs,
        time_unit=time_unit if unit_forced else "",
    )


def _detect_encoding(path: Path) -> str:
    with open(path, "rb") as fh:
        head = fh.read(HEAD_BYTES)
        fh.seek(max(0, path.stat().st_size - TAIL_BYTES))
        tail = fh.read()
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if b"\x00" in head[:4096]:
        raise LoadError(
            _("Le fichier « {} » semble être un fichier binaire (ex. .GBD Graphtec ou .UHH "
            "nanodac), pas un export texte. Exportez-le en CSV depuis l'appareil ou son "
            "logiciel.").format(path.name)
        )
    # On coupe aux fins de ligne pour ne pas tronquer un caractère multi-octets
    sample = head[: head.rfind(b"\n") + 1 or None] + tail[tail.find(b"\n") + 1:]
    for enc in ENCODINGS:
        try:
            sample.decode(enc)
            return "utf-8" if enc == "utf-8-sig" else enc
        except UnicodeDecodeError:
            continue
    return "latin-1"  # pragma: no cover - latin-1 décode tout


def _open_text(path: Path, encoding: str):
    enc = "utf-8-sig" if encoding == "utf-8" else encoding
    return open(path, "r", encoding=enc, errors="replace", newline=None)


def _head_tail_lines(path: Path, encoding: str) -> Tuple[List[str], List[str]]:
    """Premières lignes (≈ 2 Mo) et dernières lignes (≈ 256 Ko) du fichier."""
    with _open_text(path, encoding) as fh:
        head_text = fh.read(HEAD_BYTES)
    head = head_text.split("\n")
    if len(head_text) >= HEAD_BYTES:
        head = head[:-1]  # dernière ligne probablement tronquée
    if path.stat().st_size <= HEAD_BYTES or encoding == "utf-16":
        tail = head
    else:
        with open(path, "rb") as fh:
            fh.seek(path.stat().st_size - TAIL_BYTES)
            data = fh.read()
        text = data.decode(encoding, errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        tail = text.split("\n")[1:]  # première ligne tronquée
    while head and not head[-1].strip():
        head.pop()
    while tail and not tail[-1].strip():
        tail.pop()
    return head, tail


def _read_table(path: Path, encoding: str, sep: str, decimal: str, first_data: int,
                headers: List[str], unwrap: bool = False, merge_commas: bool = False):
    """Lecture rapide (moteur C de pandas) de la partie données du fichier."""
    names = list(range(len(headers)))
    text_cols = [i for i, h in enumerate(headers) if _is_time_header(h)] + [0]
    # Séparateur et décimale identiques (valeurs entre guillemets) : pandas ne sait pas
    # faire, les colonnes restent du texte et sont converties ensuite.
    pandas_decimal = "." if (merge_commas or decimal == sep) else decimal
    with _open_text(path, encoding) as fh:
        for _line in range(first_data):
            fh.readline()
        source = fh
        if unwrap or merge_commas:
            text = fh.read()
            if unwrap:
                text = _WHOLE_QUOTED_RE.sub(lambda m: m.group(1).replace('""', '"'), text)
            if merge_commas:
                text = _MERGE_COMMA_RE.sub(r"\1\2.\3", text)
            source = io.StringIO(text)
        df = pd.read_csv(
            source, sep=sep, header=None, names=names, index_col=False, decimal=pandas_decimal,
            dtype={i: str for i in text_cols}, skip_blank_lines=True, engine="c",
            on_bad_lines="skip", low_memory=False, quotechar='"',
        )
    df.columns = headers
    return df


def _lines_fully_quoted(lines: List[str]) -> bool:
    """Lignes entières entre guillemets : « "1,2026/06/01,17:01:39,+24.003" »."""
    if not lines:
        return False
    quoted = [line for line in lines if _WHOLE_QUOTED_RE.match(line.strip())]
    if len(quoted) < 0.9 * len(lines):
        return False
    return all(
        len(next(csv.reader([line], delimiter=sep))) == 1 for line in quoted[:20] for sep in SEPARATORS
    ) and any(sep in quoted[0] for sep in SEPARATORS)


def _unwrap_quotes(lines: List[str]) -> List[str]:
    out = []
    for line in lines:
        m = _WHOLE_QUOTED_RE.match(line.strip())
        out.append(m.group(1).replace('""', '"') if m else line)
    return out


def _needs_comma_merge(rows: List[List[str]]) -> bool:
    """Virgule à la fois séparateur et décimale ? (« +24 » suivi de « 003 » sur la plupart des lignes)"""
    rows = [r for r in rows if len(r) >= 3]
    if not rows or any(_POINT_DECIMAL_RE.match(f.strip()) for r in rows for f in r):
        return False
    with_pairs = sum(
        any(_SIGNED_INT_RE.match(r[i].strip()) and _DIGITS_RE.match(r[i + 1].strip()) for i in range(len(r) - 1))
        for r in rows
    )
    return with_pairs >= 0.8 * len(rows)


def _merge_decimal_commas(lines: List[str]) -> List[str]:
    return [_MERGE_COMMA_RE.sub(r"\1\2.\3", line) for line in lines]


EXCEL_MAX_ROWS = 1048576


def _declared_count(preamble: List[List[str]]) -> int:
    """Nombre de points annoncé dans l'en-tête (« Total data points,1718363 »)."""
    for row in preamble:
        cells = [c.strip().strip('"') for c in row if c.strip()]
        if len(cells) >= 2 and re.match(r"^(total data|number of data|nombre de points)", cells[0], re.IGNORECASE):
            digits = re.sub(r"[\s.,]", "", cells[1])
            if digits.isdigit():
                return int(digits)
    return 0


def _header_duration(preamble: List[List[str]]) -> Optional[float]:
    """Durée annoncée par « Start time » / « Stop time » de l'en-tête (Graphtec), en s."""
    moments = {}
    for row in preamble:
        cells = [c.strip().strip('"') for c in row if c.strip()]
        if len(cells) >= 2 and re.match(r"^(start|stop) ?time$", cells[0], re.IGNORECASE):
            text = " ".join(cells[1:3])
            moment = pd.to_datetime(text, dayfirst="/" in text and not re.match(r"^\d{4}", text), errors="coerce")
            if moment is not pd.NaT and not pd.isna(moment):
                moments[cells[0].lower().split()[0][:4]] = moment
    if "star" in moments and "stop" in moments:
        seconds = (moments["stop"] - moments["star"]).total_seconds()
        if 0 < seconds < 400 * 86400:
            return seconds
    return None


def _check_against_header(seconds: np.ndarray, preamble: List[List[str]], period: float):
    """Compare la durée lue dans les données à celle annoncée par l'en-tête (période × points,
    confirmée par début / fin si présents). Écart > 5 % : temps recalculé depuis l'en-tête."""
    count = _declared_count(preamble)
    finite = seconds[np.isfinite(seconds)]
    if not (period > 0) or count < 3 or len(finite) < 3:
        return seconds, ""
    if abs(len(seconds) - count) > max(2, 0.01 * count):
        return seconds, ""  # fichier incomplet (Excel…) : signalé ailleurs
    expected = (count - 1) * period
    stop_start = _header_duration(preamble)
    if stop_start is not None and abs(stop_start - expected) > max(2 * period, 0.02 * expected):
        return seconds, ""  # en-tête lui-même incohérent : on ne tranche pas
    read = float(finite[-1] - finite[0])
    if expected <= 0 or abs(read / expected - 1) <= 0.05:
        return seconds, ""
    rebuilt = np.where(np.isfinite(seconds), np.arange(len(seconds)) * period, np.nan)
    note = _("Temps du fichier incohérent avec son en-tête (durée lue {}, annoncée {} = {} points × {}) : temps "
             "recalculé à partir de l'en-tête.").format(_hms(read), _hms(expected), _thousands(count),
                                                     format_period(period))
    return rebuilt, note


def _hms(seconds: float) -> str:
    total = int(round(seconds))
    return "{}:{:02d}:{:02d}".format(total // 3600, total % 3600 // 60, total % 60)


def _thousands(n: int) -> str:
    return "{:,}".format(n).replace(",", " ")


def _declared_sampling(preamble: List[List[str]]) -> float:
    """Période annoncée dans l'en-tête (« Sampling interval,100ms » / « Sampling,1s »), en s."""
    for row in preamble:
        cells = [c.strip().strip('"') for c in row if c.strip()]
        if len(cells) >= 2 and cells[0].lower().startswith("sampling"):
            m = _SAMPLING_RE.match(cells[1])
            if m:
                value = float(m.group(1).replace(",", "."))
                unit = m.group(2).lower()
                return value * {"us": 1e-6, "µs": 1e-6, "ms": 1e-3, "msec": 1e-3, "s": 1.0, "sec": 1.0, "min": 60.0,
                                "h": 3600.0}[unit]
    return float("nan")


def _rebuild_coarse_time(seconds: np.ndarray, period: float):
    """Horodatage tronqué (ex. « 17:01 » sans les secondes après passage par Excel) :
    si la plupart des points ont la même heure que le précédent, le temps est recalculé
    à partir de la période annoncée dans l'en-tête. Renvoie (temps, période ou 0)."""
    if not (period > 0) or len(seconds) < 3:
        return seconds, 0.0
    diffs = np.diff(seconds[np.isfinite(seconds)])
    if len(diffs) == 0 or np.mean(diffs == 0) < 0.5:
        return seconds, 0.0
    rebuilt = np.where(np.isfinite(seconds), np.arange(len(seconds)) * period, np.nan)
    return rebuilt, period


def _is_time_header(header: str) -> bool:
    name = split_name_unit(header)[0]
    return bool(_TIME_NAME_RE.search(name) or _SUBSECOND_NAME_RE.match(name))


def _diagnostic(lines: List[str], sep: str, n_cols: int, encoding: str) -> str:
    """Résumé de ce qui a été lu, pour comprendre un fichier refusé."""
    def short(line):
        return line[:100] + ("…" if len(line) > 100 else "")

    useful = [line for line in lines if line.strip()]
    shown = useful[:4]
    for k, line in enumerate(useful):  # lignes qui suivent le marqueur « Data »
        if line.strip().strip('"').lower() == "data":
            shown += ["…"] + useful[k:k + 4]
            break
    return (
        _("Diagnostic : encodage {}, séparateur {}, {} colonnes.\n"
        "Extrait :\n{}\n\n"
        "Envoyez un extrait de ce fichier pour une correction rapide.").format(
            encoding, SEPARATOR_NAMES.get(sep, repr(sep)), n_cols, "\n".join(short(x) for x in shown))
    )


def write_anonymized_extract(path, out_path, head_lines: int = 60, tail_lines: int = 10, seed: int = 0) -> Path:
    """Extrait ANONYMISÉ : début et fin du fichier avec en-têtes, séparateurs, dates, heures,
    colonne temps et format des nombres conservés ; valeurs de mesure remplacées par des nombres
    aléatoires de même écriture. De quoi corriger un format sans divulguer de données."""
    path, out_path = Path(path), Path(out_path)
    rng = np.random.default_rng(seed)
    if is_gbd(path):  # binaire Graphtec : seul l'en-tête texte (réglages), pas les mesures
        _tree, _size, text = read_gbd_header(path)
        out_path.write_text(_ANONYMIZED_NOTE + text, encoding="utf-8")
        return out_path
    encoding = _detect_encoding(path)
    head, tail = _head_tail_lines(path, encoding)
    lines = head[:head_lines] + (["[...]"] + tail[-tail_lines:] if len(head) > head_lines else [])
    sep = max(SEPARATORS, key=lambda c: sum(line.count(c) for line in lines[-20:]))
    rows = list(csv.reader(lines, delimiter=sep))  # guillemets respectés (« "12,34" » décimal)
    data = [i for i, r in enumerate(rows) if sum(_is_value(c) for c in r) >= 2]
    widths = Counter(len(rows[i]) for i in data)
    width = widths.most_common(1)[0][0] if widths else 0
    data = [i for i in data if len(rows[i]) == width]  # lignes de mesure (pas les réglages du préambule)
    # Colonnes de temps conservées : nommées comme telles (Date, Time, ms, us…) ou croissantes
    # (temps écoulé, compteurs) ; elles servent au diagnostic
    keep = set()
    for i in range(len(rows)):
        if i not in data and len(rows[i]) == width:  # lignes d'en-tête (noms, unités)
            keep |= {j for j, name in enumerate(rows[i])
                     if _is_time_header(name.strip().strip('"')) or _INDEX_NAME_RE.match(name.strip().strip('"'))}
    for j in range(width):
        values = [_to_float(rows[i][j]) for i in data if j < len(rows[i])]
        values = [v for v in values if v is not None]
        if len(values) > 2 and all(b >= a for a, b in zip(values, values[1:])):
            keep.add(j)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=sep, lineterminator="\n")
    for i, r in enumerate(rows):
        if i in data:
            r = [c if j in keep or not _NUMBER_RE.match(_compact(c)) else _scramble(c, rng) for j, c in enumerate(r)]
        writer.writerow(r)
    out_path.write_text(_ANONYMIZED_NOTE + buffer.getvalue(), encoding="utf-8")
    return out_path


_ANONYMIZED_NOTE = ("# Extrait anonymisé par Analyser DATA : valeurs de mesure remplacées par des nombres aléatoires "
                    "de même écriture ; en-têtes, dates, heures et colonne temps conservés.\n")


def _to_float(cell: str) -> Optional[float]:
    try:
        return float(_compact(cell).replace(",", "."))
    except ValueError:
        return None


def _scramble(cell: str, rng) -> str:
    """Chiffres remplacés au hasard, signe, séparateur décimal, espaces et longueur conservés."""
    return re.sub(r"\d", lambda _m: str(rng.integers(0, 10)), cell)


def write_extract(path, out_path, head_lines: int = 80, tail_lines: int = 10) -> Path:
    """Copie le début et la fin d'un fichier (octets bruts) : de quoi analyser son format."""
    path, out_path = Path(path), Path(out_path)
    with open(path, "rb") as fh:
        head = fh.read(HEAD_BYTES).split(b"\n")[:head_lines]
        size = path.stat().st_size
        fh.seek(max(0, size - 64 * 1024))
        tail = fh.read().split(b"\n")[-tail_lines - 1:]
    with open(out_path, "wb") as out:
        out.write(b"\n".join(head))
        if size > HEAD_BYTES or len(head) >= head_lines:
            out.write(b"\n[...]\n" + b"\n".join(tail))
    return out_path


def _looks_space_separated(lines: List[str]) -> bool:
    """Lignes de mesure alignées par des espaces : même nombre (>= 2) de nombres par ligne."""
    rows = [line.split() for line in lines[-50:] if line.strip()]
    if len(rows) < 2:
        return False
    width = Counter(len(r) for r in rows).most_common(1)[0][0]
    data = [r for r in rows if len(r) == width and sum(_is_value(f) for f in r) >= 2]
    return width >= 2 and len(data) >= 0.6 * len(rows)


def _trailing_data_block(lines: List[str], separators=SEPARATORS) -> Tuple[Optional[str], List[str]]:
    """Séparateur et dernières lignes de mesure : le bloc ininterrompu de lignes, en fin de
    fichier, qui ont toutes le même nombre de champs (au moins 2) que la dernière ligne. À
    égalité, le séparateur prioritaire l'emporte (« ; » avant « , », qui peut être un décimal).
    Indépendant de la longueur du préambule, même pour un enregistrement de 5 lignes."""
    best, best_block = None, []
    for sep in separators:
        rows = list(csv.reader(lines, delimiter=sep))
        while rows and sum(_is_value(f) for f in rows[-1]) < 2:
            rows.pop()  # lignes de fin sans mesures (bilan, commentaire) : ignorées ici
        if not rows or len(rows[-1]) < 2:  # 2 colonnes suffisent (temps ; valeur)
            continue
        width, run = len(rows[-1]), 0
        for row in reversed(rows):
            if len(row) != width:
                break
            run += 1
        if run > len(best_block):
            best, best_block = sep, lines[len(rows) - run:len(rows)]
    return (best, best_block) if len(best_block) >= 2 else (None, [])


def _detect_separator(sample: List[str], filename: str, head: List[str]) -> str:
    # Lignes de données (début + fin du fichier) : le préambule ne doit pas compter.
    for sep in SEPARATORS:
        counts = [len(r) for r in csv.reader(sample, delimiter=sep)]
        value, freq = Counter(counts).most_common(1)[0]
        if value >= 2 and freq >= 0.6 * len(counts):
            return sep
    raise LoadError(
        _("Impossible de détecter le séparateur de colonnes du fichier « {} » "
        "(attendu : « ; », « , » ou tabulation, avec au moins une colonne temps "
        "et une voie de mesure).\n\n{}").format(filename, _diagnostic(head, ",", 0, "?"))
    )


def _modal_field_count(rows: List[List[str]]) -> int:
    counts = [len(r) for r in rows if r]
    return Counter(counts).most_common(1)[0][0]


def _detect_decimal(rows: List[List[str]], sep: str, n_cols: int) -> str:
    # Avec « , » comme séparateur, une décimale « , » n'est possible qu'entre guillemets :
    # csv.reader les a retirés, la virgule apparaît alors dans le champ.
    for r in rows:
        if len(r) == n_cols and any(_DECIMAL_COMMA_RE.match(_compact(f)) for f in r):
            return ","
    return "."


def _is_value(field_: str) -> bool:
    f = field_.strip().strip('"').lstrip("'")
    return bool(f) and bool(_NUMBER_RE.match(_compact(f)) or _DATETIME_LIKE_RE.match(f) or _OVER_RANGE_RE.match(f))


def _value_signature(rows: List[List[str]], n_cols: int) -> set:
    """Colonnes contenant une valeur dans (presque) toutes les lignes de mesure."""
    data = [r for r in rows if len(r) == n_cols]
    if not data:
        return set()
    return {j for j in range(n_cols) if sum(_is_value(r[j]) for r in data) >= 0.9 * len(data)}


def _is_data_row(row: List[str], n_cols: int, signature: Optional[set] = None) -> bool:
    """Ligne de mesure ?

    Les colonnes d'alarme ("LLLL"), de messages ou les voies débranchées ne doivent pas
    faire rejeter la ligne. Mais une ligne d'en-tête complétée par Excel
    (« Start time;01/06/2026;17:01:39;;;;;; ») ne doit pas passer pour une mesure :
    on vérifie qu'elle a des valeurs aux mêmes colonnes que les vraies lignes de données.
    """
    if len(row) != n_cols:
        return False
    if signature and len(signature) >= 2:
        hits = sum(_is_value(row[j]) for j in signature)
        return hits >= max(2, math.ceil(0.8 * len(signature)))
    return sum(_is_value(f) for f in row) >= 2


def _find_first_data_row(rows, n_cols, signature: Optional[set] = None) -> Optional[int]:
    for i in range(len(rows)):
        if _is_data_row(rows[i], n_cols, signature) and all(
            _is_data_row(rows[j], n_cols, signature) for j in range(i + 1, min(i + 3, len(rows)))
        ):
            # Début d'enregistrement avec des voies vides (capteurs pas encore branchés) :
            # ces lignes ont l'horodatage mais pas toutes les valeurs ; ce sont des mesures.
            while i > 0 and _is_partial_data_row(rows[i - 1], n_cols, signature):
                i -= 1
            return i
    return None


def _is_partial_data_row(row: List[str], n_cols: int, signature: Optional[set]) -> bool:
    """Ligne de mesure dont certaines voies sont vides : au moins 2 valeurs, et aucun texte
    (en-tête, unité, « Start time ») dans les colonnes qui portent des valeurs."""
    if len(row) != n_cols or not signature:
        return False
    cells = [row[j].strip().strip('"') for j in sorted(signature)]
    return sum(_is_value(c) for c in cells) >= 2 and all(not c or _is_value(c) for c in cells)


_UNIT_CELL_RE = re.compile(r"^[%°ºµ\u2103\ufffdÂA-Za-z/.\- ]{0,8}$")  # « ℃ », degré mal décodé compris


_ALARM_LABEL_RE = re.compile(r"^[A-Za-z]\d{3,}$")  # « A1234567890 » (GL860)


_TIME_FORMAT_CELL_RE = re.compile(r"^[YyMmDdHhSsUuµ/:.,\- ]{2,24}$")  # « yyyy/mm/dd », « hh:mm:ss »


def _looks_like_unit_row(row: List[str]) -> bool:
    """Ligne d'unités sous les en-têtes : « "","","","V","mV","degC" », « NO.,Time,ms,V,mA »
    ou « No.,yyyy/mm/dd,hh:mm:ss,us,V,A » (formats de date / heure dans les colonnes temps)."""
    cells = [c.strip().strip('"').strip("()[]") for c in row]
    filled = [c for c in cells if c]
    if not filled or any(_NUMBER_RE.match(c) for c in filled):
        return False
    return all(_UNIT_CELL_RE.match(c) or _ALARM_LABEL_RE.match(c) or _TIME_FORMAT_CELL_RE.match(c)
               for c in filled)


def _find_headers(rows, first_data, n_cols):
    """Renvoie (noms de colonnes, unités, alias) à partir des lignes qui précèdent les données.

    Cas gérés : une ligne d'en-têtes ; en-têtes + ligne d'unités (Graphtec) ; en-tête
    suivi d'un descriptif de voie en bout de ligne (nanodac :
    « Date/Heure  Channel 2  (ENAN2);Group 1;M402-M210;°C »).
    """
    units, aliases = {}, {}
    default = ["Temps"] + [_("Voie {}").format(i + 1) for i in range(1, n_cols)]
    if first_data < 1:
        return default, units, aliases
    candidate = [c.strip().strip('"') for c in rows[first_data - 1]]
    extra = []
    if len(candidate) > n_cols:
        candidate = _regroup_descriptors(candidate, n_cols)
    if len(candidate) > n_cols:  # descriptif en bout de ligne
        extra = [c for c in candidate[n_cols:] if c]
        candidate = candidate[:n_cols]
    if len(candidate) != n_cols or _is_data_row(candidate, n_cols):
        return default, units, aliases

    if _looks_like_unit_row(candidate) and first_data >= 2 and len(rows[first_data - 2]) >= n_cols:
        headers = _dedupe([h.strip().strip('"') for h in rows[first_data - 2][:n_cols]])
        for h, u in zip(headers, candidate):
            u = u.strip("()[]")
            if u and not _ALARM_LABEL_RE.match(u):
                units[h] = normalize_unit(u)
        return headers, units, aliases

    cleaned = []
    for h in candidate:
        name, unit, alias = _split_descriptor(h)
        cleaned.append(name)
        if unit:
            units[name] = unit
        if alias:
            aliases[name] = alias
    headers = _dedupe(cleaned)
    value_cols = [h for h in headers if not _is_time_header(h) and not _INDEX_NAME_RE.match(h)]
    if extra and len(value_cols) == 1:
        unit, alias = _channel_descriptor(extra)
        if unit:
            units[value_cols[0]] = unit
        if alias:
            aliases[value_cols[0]] = alias
    return headers, units, aliases


def _is_unit_token(cell: str) -> bool:
    unit = normalize_unit(cell.strip())
    return bool(unit) and (quantity_of(unit) != OTHER or unit == "%")


def _regroup_descriptors(cells: List[str], n_cols: int) -> List[str]:
    """Descriptifs nanodac éclatés sur plusieurs cellules (classeur Excel coupé aussi au « ; »,
    issue #16) : « Date/Heure | Channel 2 (ENAN2) | Group 1 | M402-M210 | °C | Channel 3 … | %HR »
    -> « Date/Heure | Channel 2 (ENAN2);Group 1;M402-M210;°C | Channel 3 …;%HR ».

    Ne regroupe que si chaque voie se termine par une unité et que le compte tombe juste ;
    sinon les cellules sont rendues telles quelles."""
    cells = list(cells)
    while cells and not cells[-1]:
        cells.pop()
    if not cells or not _is_time_header(cells[0]):
        return cells
    groups, current = [], []
    for c in cells[1:]:
        current.append(c)
        if _is_unit_token(c):
            groups.append(";".join(x for x in current if x))
            current = []
    if current or len(groups) != n_cols - 1 or any(";" not in g for g in groups):
        return cells
    return [cells[0]] + groups


def _split_descriptor(header: str) -> Tuple[str, str, str]:
    """« Channel 2   (ENAN2);Group 1;M402-M210;°C » -> ("Channel 2", "°C", "M402-M210").

    Les en-têtes sans « ; » sont renvoyés tels quels.
    """
    if ";" not in header:
        return header, "", ""
    tokens = [t.strip() for t in header.split(";") if t.strip()]
    name = re.sub(r"\s*\([^)]*\)\s*$", "", tokens[0]).strip() or tokens[0]
    unit, alias = _channel_descriptor(tokens[1:])
    return name, unit, alias


def _channel_descriptor(cells: List[str]) -> Tuple[str, str]:
    """« (ENAN2);Group 1;M402-M210;°C » -> ("°C", "M402-M210")."""
    tokens = [t.strip() for t in re.split(r"[;\t]", ";".join(cells)) if t.strip()]
    for k in range(len(tokens) - 1, -1, -1):
        unit = normalize_unit(tokens[k])
        if quantity_of(unit) != OTHER or unit == "%":
            alias = tokens[k - 1] if k >= 1 else ""
            if alias.startswith("(") or re.match(r"^group\b", alias, re.IGNORECASE):
                alias = ""
            return unit, alias
    return "", ""


def _graphtec_amp_settings(preamble: List[List[str]]):
    """Tableau « Amp settings » des centrales Graphtec : {"CH1": ("nom du signal", "unité")}."""
    out = {}
    columns = None
    for row in preamble:
        cells = [c.strip().strip('"') for c in row]
        lowered = [c.lower() for c in cells]
        if lowered and lowered[0] == "ch" and "signal name" in lowered:
            columns = lowered
            continue
        if columns and cells and re.match(r"^CH\d", cells[0], re.IGNORECASE):
            info = dict(zip(columns, cells))
            if info.get("input", "").lower() == "off":
                continue
            out[cells[0]] = (info.get("signal name", ""), normalize_unit(info.get("unit", "")))
        elif columns and cells and cells[0]:
            columns = None  # fin du tableau
    return out


def _dedupe(names: List[str]) -> List[str]:
    seen = {}
    out = []
    for n in names:
        base = n or "Colonne"
        if base in seen:
            seen[base] += 1
            out.append("{} ({})".format(base, seen[base]))
        else:
            seen[base] = 1
            out.append(base)
    return out


def _unique(label: str, existing) -> str:
    if label not in existing:
        return label
    i = 2
    while "{} #{}".format(label, i) in existing:
        i += 1
    return "{} #{}".format(label, i)


def _spread_sample(column: pd.Series, size: int = 2000) -> pd.Series:
    """Valeurs réparties sur toute la colonne, sans les marques hors échelle : une voie qui
    commence par des « +++++++ » / « BURNOUT » (capteur pas encore branché) reste une voie."""
    values = column.dropna()
    if len(values) > size:
        values = values.iloc[:: max(1, len(values) // size)]
    text = values.astype(str).str.strip()
    return values[~text.str.match(_OVER_RANGE_PATTERN) & (text != "")]


def _to_numeric(series: pd.Series, decimal: str) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    if decimal == ".":
        fast = pd.to_numeric(series, errors="coerce")  # conversion C, sans traitement de texte
        if fast.notna().sum() >= 0.99 * series.notna().sum():
            return fast.astype(float)
    s = series.fillna("").astype(str).str.replace(_INNER_SPACES, "", regex=True)
    if decimal == ",":
        s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def _extract_time(raw: pd.DataFrame, decimal: str, filename: str, time_unit: Optional[str] = None):
    """Renvoie (temps écoulé en secondes, horodatage de départ ou None, colonnes utilisées,
    compteur d'échantillons détecté, unité imposée appliquée ou non, note sur cette unité)."""
    cols = list(raw.columns)
    names = {c: split_name_unit(c)[0] for c in cols}
    time_cols = [c for c in cols if _TIME_NAME_RE.search(names[c]) and not _SUBSECOND_NAME_RE.match(names[c])]
    ms_cols = [c for c in cols if _SUBSECOND_NAME_RE.match(names[c])]

    if not time_cols:
        # Pas de nom explicite : la première colonne non-index est le temps
        candidates = [c for c in cols if not _INDEX_NAME_RE.match(c.strip())]
        if not candidates:
            raise LoadError(_("Aucune colonne temps trouvée dans « {} ».").format(filename))
        time_cols = [candidates[0]]

    used = set(time_cols[:1])
    main = time_cols[0]
    series = raw[main]

    # Date et heure dans deux colonnes séparées ("Date" ; "Heure")
    if _DATE_ONLY_NAME_RE.match(names[main]) and len(time_cols) > 1:
        other = time_cols[1]
        dates = raw[main].fillna("")
        # Classeur Excel (issue #16) : une cellule date est lue « 2026-11-02 00:00:00.000 »
        dates = dates.str.replace(_MIDNIGHT_SUFFIX_RE, "", regex=True) \
            if dates.head(200).str.contains(_MIDNIGHT_SUFFIX_RE, regex=True).any() else dates
        clock = raw[other].fillna("")
        if (not dates.str.contains(":", regex=False).any()
                and clock.str.contains(":", regex=False).any()):
            if clock[clock != ""].head(200).str.match(_ISO_DAY_RE).mean() > 0.9:
                series = clock  # la colonne « Heure » contient déjà la date complète
            else:
                series = dates + " " + clock
            used.add(other)

    unit_note = ""
    if time_unit:
        sample = series.dropna().astype(str).str.strip().str.lstrip("'")
        sample = sample[sample != ""].head(200)
        if len(sample) and _to_numeric(sample, decimal).notna().mean() > 0.9:
            unit_note = _(
                "Unité du temps imposée à l'import : colonne « {} » lue en {}.").format(main, _(TIME_UNIT_NAMES[time_unit]))
        else:
            unit_note = _("Unité du temps « {} » ignorée : la colonne « {} » contient des dates / heures, "
                          "pas un temps écoulé en nombres.").format(_(TIME_UNIT_NAMES[time_unit]), main)
            time_unit = None
    seconds, start, counter = _parse_time(series, main, decimal, filename, time_unit)
    if counter and _TIME_NAME_RE.search(names[main]) and not _COUNTER_NAME_RE.search(names[main]):
        counter = False  # « Temps » en secondes entières : c'est bien un temps

    # Graphtec : colonne "ms" séparée qui complète l'horodatage à la seconde
    if ms_cols and start is not None:
        sub = _to_numeric(raw[ms_cols[0]], decimal)
        if sub.notna().mean() > 0.9:
            factor = _SUBSECOND_FACTOR[split_name_unit(ms_cols[0])[0].lower()]
            seconds = seconds + sub.fillna(0).to_numpy(dtype=float) * factor
            used.add(ms_cols[0])
            counter = False

    for c in cols:  # les autres colonnes date/heure ne sont pas des voies
        if c != main and _TIME_NAME_RE.search(names[c]):
            head = raw[c].dropna().head(200)
            if _to_numeric(head, decimal).notna().mean() < 0.5:
                used.add(c)
    return seconds, start, used, (main if counter else ""), bool(time_unit), unit_note


def _parse_time(series: pd.Series, header: str, decimal: str, filename: str, time_unit: Optional[str] = None):
    name, unit = split_name_unit(header)
    s = series.fillna("").astype(str)
    # Le type de la colonne est décidé sur un échantillon : les traitements de texte
    # sur des millions de lignes ne sont faits que s'ils sont vraiment nécessaires.
    raw_sample = s[s != ""].head(200)
    sample = raw_sample.str.strip().str.lstrip("'")
    if (sample != raw_sample).any():
        s = s.str.strip().str.lstrip("'")
    if sample.empty:
        raise LoadError(_("La colonne temps « {} » du fichier « {} » est vide.").format(header, filename))

    if _to_numeric(sample, decimal).notna().mean() > 0.9:
        numeric = _to_numeric(s, decimal)
        values = numeric.to_numpy(dtype=float)
        first = values[np.isfinite(values)][0]
        diffs = np.diff(values[np.isfinite(values)])
        step = float(np.median(diffs)) if len(diffs) else 0.0
        unit_key = unit.lower()
        if time_unit:  # choisie dans la fenêtre Import (issue #18)
            return (values - first) * _TIME_UNITS_S[time_unit], None, False
        if unit_key in _TIME_UNITS_S:
            factor = _TIME_UNITS_S[unit_key]
        elif re.search(r"\bms\b|_ms$", name, re.IGNORECASE):
            factor = 1e-3
        elif re.search(r"\bmin\b|_min$", name, re.IGNORECASE):
            factor = 60.0
        elif 20000 < first < 80000 and 0 < step < 1:
            # Date série Excel (jours depuis le 30/12/1899)
            start = pd.Timestamp("1899-12-30") + pd.to_timedelta(first, unit="D")
            # arrondi à la ms : les dates série n'ont que ~8 décimales
            return np.round((values - first) * 86400.0, 3), start.round("ms"), False
        else:
            factor = 1.0  # secondes par défaut
            # Compteur d'échantillons : entiers qui augmentent de 1 à chaque ligne, sans unité
            finite = values[np.isfinite(values)]
            if (len(finite) > 2 and step == 1.0 and np.all(finite == np.round(finite))
                    and np.mean(np.diff(finite) == 1) > 0.99):
                return values - first, None, True
        return (values - first) * factor, None, False

    if np.mean([bool(_DURATION_RE.match(v)) for v in sample]) > 0.9:
        td = pd.to_timedelta(s.str.replace(",", ".", regex=False), errors="coerce")
        secs = _unwrap_midnight(td.dt.total_seconds().to_numpy(dtype=float))
        return secs - secs[np.isfinite(secs)][0], None, False

    # Durées écrites autrement (issue #16) : « 05:30,2 » (MM:SS), « 1 j 02:03:04 », « 1.02:03:04 »,
    # « 10 s », « 1h02m03s »… — à tester AVANT la date/heure, qui les prendrait pour des heures du jour
    if np.mean([bool(_DURATION_LIKE_RE.match(v)) for v in sample]) > 0.9:
        flexible = _flexible_seconds(s)
        if flexible is not None:
            return flexible - flexible[np.isfinite(flexible)][0], None, False

    # Date/heure absolue. "2026/10/01" -> année en tête ; "01/10/2026" -> jour en tête.
    if any(_COMMA_SECONDS_RE.search(v) for v in sample):  # « 17:01:39,500 »
        s = s.str.replace(",", ".", regex=False)
    dt = _to_datetime(s)
    if dt.notna().mean() < 0.9:
        # Autres écritures du temps (issue #16) : MM:SS, jours, unités dans les cases, virgule…
        flexible = _flexible_seconds(s)
        if flexible is not None:
            return flexible - flexible[np.isfinite(flexible)][0], None, False
        examples = ", ".join("« {} »".format(v[:24]) for v in sample.head(3))
        raise LoadError(
            _("La colonne temps « {} » du fichier « {} » n'est pas reconnue "
            "(attendu : date/heure, H:MM:SS ou temps écoulé en ms / s / min).").format(header, filename)
            + _("\n\nValeurs lues : {}").format(examples)
        )
    start = dt[dt.notna()].iloc[0]
    secs = (dt - start).dt.total_seconds().to_numpy(dtype=float)
    return secs, start, False


# Formats de date/heure courants, essayés d'abord : bien plus rapide que la détection
# automatique de pandas sur des millions de lignes.
_DATETIME_FORMATS = (
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S.%f", "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d/%m/%y %H:%M:%S",
)


_DAYS_PREFIX_RE = re.compile(r"^(\d+)\s*(?:j|d|jours?|days?)\.?\s+|^(\d+)\.(?=\d{1,2}:)", re.IGNORECASE)
_CLOCK_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)(?::(\d{1,3}))?$")  # H:MM:SS(:mmm)
_DURATION_LIKE_RE = re.compile(
    r"^\s*(\d+\s*(j|d|jours?|days?)\.?\s+\S"  # jours + heure
    r"|\d+\.\d{1,2}:\d{2}"  # 1.02:03:04
    r"|\d{1,3}:\d{2}[.,]\d+\s*$"  # MM:SS,f
    r"|\d+:\d{2}:\d{2}:\d{1,3}\s*$"  # H:MM:SS:mmm
    r"|(\d+([.,]\d+)?\s*(ms|msec|s|sec|min|mn|m|h|hr)\s*)+$)", re.IGNORECASE)  # « 1h02m03s » aussi
_UNIT_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|msec|s|sec|secondes?|min|mn|minutes?|m|h|hr|heures?|hours?|j|d)(?![a-zé])",
                            re.IGNORECASE)
_UNIT_SECONDS = {"ms": 1e-3, "msec": 1e-3, "s": 1, "sec": 1, "seconde": 1, "secondes": 1, "min": 60, "mn": 60,
                 "minute": 60, "minutes": 60, "m": 60, "h": 3600, "hr": 3600, "heure": 3600, "heures": 3600,
                 "hour": 3600, "hours": 3600, "j": 86400, "d": 86400}


def _one_duration(text: str) -> float:
    """« 1 j 02:03:04,5 », « 05:30.2 », « 1h02m03s », « 1,5 min », « 12,3 » -> secondes (nan sinon)."""
    t = text.strip().replace(",", ".").lower()
    if not t:
        return np.nan
    days = 0.0
    prefix = _DAYS_PREFIX_RE.match(t)
    if prefix:
        days = float(prefix.group(1) or prefix.group(2))
        t = t[prefix.end():].strip()
    clock = _CLOCK_RE.match(t)
    if clock:
        hours = float(clock.group(1)) if clock.group(1) else 0.0
        millis = float(clock.group(4)) / 1000 if clock.group(4) else 0.0
        return days * 86400 + hours * 3600 + float(clock.group(2)) * 60 + float(clock.group(3)) + millis
    tokens = _UNIT_TOKEN_RE.findall(t)
    if tokens and re.fullmatch(r"(\s*\d+(?:\.\d+)?\s*[a-zé]+\s*)+", t):
        return days * 86400 + sum(float(v) * _UNIT_SECONDS[u.lower()] for v, u in tokens)
    try:
        return days * 86400 + float(t.replace(" ", ""))
    except ValueError:
        return np.nan


def _flexible_seconds(s: pd.Series) -> Optional[np.ndarray]:
    """Lecture tolérante d'une colonne temps (dernier recours), ou None si elle reste illisible."""
    sample = s[s != ""].head(200)
    if not len(sample) or np.mean([np.isfinite(_one_duration(v)) for v in sample]) < 0.9:
        return None
    values = np.array([_one_duration(v) for v in s], dtype=float)
    return _unwrap_midnight(values)


def _unwrap_midnight(seconds: np.ndarray) -> np.ndarray:
    """Heure du jour sans date qui repasse par 00:00:00 (essai sur plusieurs jours) : +24 h."""
    finite = np.isfinite(seconds)
    if finite.sum() < 2:
        return seconds
    values = seconds[finite]
    jumps = np.diff(values) < -12 * 3600
    if not jumps.any():
        return seconds
    out = seconds.copy()
    out[finite] = values + np.concatenate(([0], np.cumsum(jumps))) * 86400.0
    return out


_US_DATETIME_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S.%f",
    "%m/%d/%Y %H:%M", "%m/%d/%y %I:%M:%S %p", "%m/%d/%y %H:%M:%S", "%m-%d-%Y %H:%M:%S",
)
_DAY_MONTH_RE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}")


def _date_order(s: pd.Series) -> Optional[str]:
    """Ordre jour / mois des dates « 01/11/2026 » d'après TOUTE la colonne (issue #16) : un 1er
    nombre > 12 -> jour en tête ; un 2e nombre > 12 -> mois en tête (dates américaines). Les
    seules premières lignes ne suffisent pas : un essai du 30/10 au 02/11 change de mois."""
    filled = s[s != ""]
    step = max(1, len(filled) // 5000)
    firsts, seconds = [], []
    for value in filled.iloc[::step]:
        match = _DAY_MONTH_RE.match(value)
        if match:
            firsts.append(int(match.group(1)))
            seconds.append(int(match.group(2)))
    if not firsts:
        return None
    if max(firsts) > 12 >= max(seconds):
        return "day"
    if max(seconds) > 12 >= max(firsts):
        return "month"
    if len(set(zip(firsts, seconds))) > 1:  # plusieurs dates différentes (sinon : rien à départager)
        # Ambigu (ex. du 01/11 au 04/11) : la lecture qui donne le temps le plus régulier
        # (pas de saut de plusieurs mois entre deux lignes) l'emporte ; à égalité, jour en tête
        values = filled.iloc[::step]
        jumps = {}
        for order, dayfirst in (("day", True), ("month", False)):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                parsed = _mixed_datetime(values, dayfirst)
            seconds_ = parsed.dropna().astype("int64").to_numpy() / 1e9
            jumps[order] = float(np.abs(np.diff(seconds_)).max()) if len(seconds_) > 1 else 0.0
        return "month" if jumps["month"] < 0.5 * jumps["day"] else None
    return None


# pandas >= 2 : format="mixed" (chaque valeur lue à part). pandas 1.x (Anaconda plus ancien) ne le
# connaît pas et renvoie des dates vides SANS erreur : on y laisse la détection automatique.
_PANDAS_2 = int(pd.__version__.split(".")[0]) >= 2


def _mixed_datetime(values: pd.Series, dayfirst: bool) -> pd.Series:
    """Dates écrites de façons différentes dans une même colonne (NaT si illisible)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if _PANDAS_2:
            return pd.to_datetime(values, format="mixed", dayfirst=dayfirst, errors="coerce")
        return pd.to_datetime(values, dayfirst=dayfirst, errors="coerce")


def _to_datetime(s: pd.Series) -> pd.Series:
    sample = s[s != ""].head(50)
    order = _date_order(s)
    formats = _US_DATETIME_FORMATS + _DATETIME_FORMATS if order == "month" else _DATETIME_FORMATS
    if order == "month" or any(re.search(r"\b[AP]M\b", v, re.IGNORECASE) for v in sample):
        formats = formats + _US_DATETIME_FORMATS if order != "month" else formats
    for fmt in formats:
        if order == "month" and fmt.startswith("%d"):
            continue  # dates américaines prouvées : jamais jour en tête
        if order == "day" and fmt.startswith("%m"):
            continue
        try:
            pd.to_datetime(sample, format=fmt)
        except (ValueError, TypeError):
            continue
        parsed = pd.to_datetime(s, format=fmt, errors="coerce")
        # Écritures mêlées dans le fichier (« …:10 » puis « …:10,5 », minuit écrit sans l'heure
        # par Excel…) : ces lignes ne doivent pas disparaître et faire un trou (issue #16)
        missing = parsed.isna() & (s != "")
        if missing.any():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                parsed[missing] = _mixed_datetime(s[missing], order != "month" and not fmt.startswith("%m"))
        return parsed
    # Format inhabituel : détection automatique (plus lente)
    first_value = sample.iloc[0] if len(sample) else ""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pd.to_datetime(s, dayfirst=order != "month" and not _YEAR_FIRST_RE.match(first_value),
                              errors="coerce")


def _gap_notes(seconds: np.ndarray, start: Optional[pd.Timestamp], limit: int = 3) -> List[str]:
    """Interruptions de l'enregistrement (aucune mesure pendant plus de 10 périodes et 1 min),
    signalées pour distinguer un vrai trou du fichier d'une erreur de lecture (issue #16)."""
    finite = seconds[np.isfinite(seconds)]
    if len(finite) < 3:
        return []
    diffs = np.diff(finite)
    period = float(np.median(diffs[diffs > 0])) if (diffs > 0).any() else 0.0
    if period <= 0:
        return []
    gaps = np.flatnonzero(diffs > max(10 * period, 60.0))
    notes = []
    for i in gaps[:limit]:
        at = (start + pd.Timedelta(seconds=float(finite[i]))).strftime("%d/%m/%Y %H:%M:%S") if start is not None \
            else _hms(float(finite[i]))
        notes.append(_("Interruption de l'enregistrement : aucune mesure pendant {} après {} (le fichier ne "
                       "contient pas de données à cet endroit).").format(_hms(float(diffs[i])), at))
    if len(gaps) > limit:
        notes.append(_("… et {} autre(s) interruption(s).").format(len(gaps) - limit))
    return notes


def _sampling_period(seconds: np.ndarray) -> float:
    diffs = np.diff(seconds)
    diffs = diffs[diffs > 0]
    return float(np.median(diffs)) if len(diffs) else float("nan")


def _guess_source(filename: str, preamble: List[str]) -> str:
    text = (filename + " " + " ".join(preamble[:30])).lower()
    for key, label in _SOURCES:
        if key in text:
            return label
    if re.search(r"\bgl\d{3}", text):
        return "Graphtec"
    if re.search(r"(^|[_\- ])temp", filename.lower()):
        return "Clim"
    return "Générique"
