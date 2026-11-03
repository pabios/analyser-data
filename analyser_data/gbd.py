"""Lecture des fichiers binaires natifs Graphtec .GBD (GL220 / GL820 / GL860 / GL980…) — issue #16.

Structure (d'après la fiche de spécification Graphtec GL220/GL820, reprise par des lecteurs
libres : gist astropenguin « gbd2csv », paquet R whatalnk/readgbd) :

* **En-tête texte** (Shift-JIS) de ``HeaderSiz`` octets, en sections : ``$Common``, ``$$Data``
  (``Counts`` = nombre d'enregistrements, ``Order`` = ordre des colonnes, ``Sample`` = période),
  ``$$Time`` (``Start``…), ``$Amp`` (une ligne par voie : type, entrée, gamme…),
  puis ``$EndHeader``.
* **Données binaires** juste après, enregistrement par enregistrement, en big-endian :
  voies ``CHn`` sur 16 bits signés ; ``Pulse`` sur 32 bits ; ``Logic`` / ``Alarm`` / ``AlOut`` /
  ``Status`` sur 16 bits non signés.
* **Conversion** : voie en tension DC -> valeur = brut × gamme / 20 000 (ex. gamme 20 V : ±20 000
  points = ±20 V) ; voie de température -> brut / 10 °C ; ±32 767 = hors échelle.

Les fichiers sont ensuite traités comme un export CSV (mêmes voies « Channel N », même temps).
Lecture marquée « à vérifier » tant qu'elle n'a pas été comparée à l'export CSV de l'appareil.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .i18n import _

OVER_RANGE = 32764  # |brut| >= cette valeur : hors échelle / BURNOUT
_SECTION_RE = re.compile(r"^(\$+)\s*(\S.*?)\s*$")
_RANGE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mV|V|kV)\s*$", re.IGNORECASE)


class GbdError(Exception):
    """Fichier GBD illisible (message destiné à l'utilisateur)."""


def is_gbd(path) -> bool:
    path = Path(path)
    if path.suffix.lower() == ".gbd":
        return True
    try:
        with open(path, "rb") as fh:
            return fh.read(16).lstrip().startswith(b"$Common")
    except OSError:
        return False


def read_header(path: Path) -> Tuple[Dict, int, str]:
    """En-tête : (sections imbriquées, taille de l'en-tête en octets, texte brut)."""
    with open(path, "rb") as fh:
        start = fh.read(4096)
    match = re.search(rb"HeaderSiz\s*=\s*(\d+)", start)
    if not match:
        raise GbdError(_("« {} » n'est pas un fichier GBD Graphtec lisible (taille d'en-tête introuvable).").format(
            path.name))
    size = int(match.group(1))
    with open(path, "rb") as fh:
        raw = fh.read(size)
    text = raw.replace(b"\x00", b"").decode("shift_jis", errors="replace")
    end = text.find("$EndHeader")
    if end >= 0:
        text = text[:end]
    return _parse_sections(text), size, text


def _parse_sections(text: str) -> Dict:
    """« $Common » / « $$Data » / « key = value » -> dictionnaires imbriqués (clés en minuscules)."""
    root: Dict = {}
    stack: List[Dict] = [root]
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        section = _SECTION_RE.match(line)
        if section and "=" not in line:
            depth = len(section.group(1))
            name = section.group(2).lower()
            stack = stack[:depth]
            node = stack[-1].setdefault(name, {})
            stack.append(node)
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            stack[-1][key.strip().lower()] = value.strip()
    return root


def _find(tree: Dict, *path: str) -> Optional[str]:
    """Valeur à un chemin de sections, cherchée aussi à n'importe quelle profondeur."""
    node = tree
    for key in path:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            node = None
            break
    if isinstance(node, str):
        return node
    last = path[-1]
    stack = [tree]
    while stack:
        current = stack.pop()
        for key, value in current.items():
            if key == last and isinstance(value, str):
                return value
            if isinstance(value, dict):
                stack.append(value)
    return None


def _split(value: Optional[str]) -> List[str]:
    return [v.strip().strip('"') for v in (value or "").split(",")]


def _dtype_of(name: str) -> str:
    upper = name.upper()
    if upper.startswith("CH"):
        return ">i2"
    if upper.startswith("PULSE"):
        return ">u4"
    if upper.startswith(("LOGIC", "ALARM", "ALOUT", "STATUS")):
        return ">u2"
    raise GbdError(_("Colonne « {} » inconnue dans le fichier GBD.").format(name))


def _period_seconds(sample: str) -> float:
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(us|ms|s|sec|min|h)\s*$", sample or "", re.IGNORECASE)
    if not match:
        raise GbdError(_("Période d'échantillonnage illisible dans l'en-tête GBD : « {} ».").format(sample))
    value, unit = float(match.group(1)), match.group(2).lower()
    return value * {"us": 1e-6, "ms": 1e-3, "s": 1.0, "sec": 1.0, "min": 60.0, "h": 3600.0}[unit]


def _start_time(tree: Dict) -> Optional[pd.Timestamp]:
    for key in ("trigger", "start"):
        value = _find(tree, "common", "time", key)
        if value:
            moment = pd.to_datetime(value.replace(",", " "), errors="coerce")
            if not pd.isna(moment):
                return moment
    return None


def _channel_scale(amp: Optional[str], temp_unit: str) -> Tuple[float, str]:
    """(diviseur ou multiplicateur, unité) d'une voie d'après sa ligne $Amp.

    Renvoie (facteur, unité) avec valeur = brut × facteur."""
    fields = _split(amp)
    kind = fields[0].upper() if fields else ""
    if kind.startswith("TEMP") or kind in ("TC", "PT", "RTD"):
        return 0.1, temp_unit or "°C"
    for field in fields:
        match = _RANGE_RE.match(field)
        if match:
            value, unit = float(match.group(1)), match.group(2)
            unit = {"mv": "mV", "v": "V", "kv": "kV"}[unit.lower()]
            return value / 20000.0, unit
    return 1.0, ""


def read_gbd(path) -> Tuple[pd.DataFrame, Dict]:
    """Données (colonne « Date&Time » puis une colonne par voie CH) et informations d'en-tête."""
    path = Path(path)
    tree, size, _text = read_header(path)
    counts_text = _find(tree, "common", "data", "counts")
    order = [o for o in _split(_find(tree, "common", "data", "order")) if o]
    if not counts_text or not order:
        raise GbdError(_("En-tête GBD incomplet dans « {} » (Counts / Order introuvables).").format(path.name))
    counts = int(re.sub(r"\D", "", counts_text) or 0)
    period = _period_seconds(_find(tree, "common", "data", "sample") or "")
    dtype = np.dtype([(name, _dtype_of(name)) for name in order])
    with open(path, "rb") as fh:
        fh.seek(size)
        data = fh.read()
    available = len(data) // dtype.itemsize
    if available == 0:
        raise GbdError(_("Le fichier GBD « {} » ne contient aucune mesure.").format(path.name))
    records = np.frombuffer(data, dtype=dtype, count=min(counts, available) if counts else available)

    temp_unit = (_find(tree, "common", "data", "tempunit") or "°C").replace("ﾟ", "°")
    amp = tree.get("amp", {})
    columns, units, notes = {}, [], []
    for name in order:
        if not name.upper().startswith("CH"):
            continue  # alarmes, logique, impulsions : pas des voies analogiques
        raw = records[name].astype(float)
        factor, unit = _channel_scale(amp.get(name.lower()) if isinstance(amp, dict) else None, temp_unit)
        values = raw * factor
        values[np.abs(raw) >= OVER_RANGE] = np.nan
        columns[name] = values
        units.append(unit)
    if not columns:
        raise GbdError(_("Aucune voie analogique (CH…) dans le fichier GBD « {} ».").format(path.name))

    start = _start_time(tree) or pd.Timestamp("2000-01-01")
    times = start + pd.to_timedelta(np.arange(len(records)) * period, unit="s")
    frame = pd.DataFrame({"Date&Time": times, **columns})
    if counts and available < counts:
        notes.append(_("Fichier GBD incomplet : {} mesures annoncées, {} lues.").format(counts, available))
    info = {"period": period, "counts": len(records), "units": units, "notes": notes,
            "model": _find(tree, "common", "model") or _find(tree, "common", "id") or ""}
    return frame, info
