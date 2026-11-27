"""US-02 — Voies de mesure : nettoyage des libellés, unités et grandeurs physiques.

Exemples de conversions :
    "CH1"                 -> "Channel 1"   (unité mV par défaut sur un Graphtec, gardée à part)
    "CH3 [V]"             -> "Channel 3"   (unité V)
    "Courant (A)"         -> "Courant (A)"
    "Température"         -> "Température (°C)"
    "HR (%)"              -> "HR (%HR)"
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

# Unité entre parenthèses ou crochets en fin de libellé : "Tension (V)", "CH1[mV]"
_UNIT_RE = re.compile(r"[\(\[]\s*([^\)\]]*?)\s*[\)\]]\s*$")
# Voies génériques des centrales : "CH1", "Ch 2", "CH-03", "Voie 4", "Channel5"
_CHANNEL_RE = re.compile(r"^(?:ch|ch\.|channel|voie|cv)\s*[-_ ]?\s*0*(\d+)$", re.IGNORECASE)

_UNIT_ALIASES = {
    "uv": "µV",
    "µv": "µV",
    "mv": "mV",
    "v": "V",
    "kv": "kV",
    "a": "A",
    "ma": "mA",
    "ua": "µA",
    "µa": "µA",
    "ka": "kA",
    "°c": "°C",
    "ºc": "°C",
    "degc": "°C",
    "deg c": "°C",
    "c": "°C",
    "%hr": "%HR",
    "%rh": "%HR",
    "hr": "%HR",
    "rh": "%HR",
    "%": "%",
    "w": "W",
    "mw": "mW",
    "kw": "kW",
    "ohm": "Ω",
    "ω": "Ω",
    "mohm": "mΩ",
    "mω": "mΩ",
    "kohm": "kΩ",
    "kω": "kΩ",
    # pression (issue #18) : écritures des centrales (« MBAR », « Kpa », « psia »…)
    "pa": "Pa",
    "hpa": "hPa",
    "kpa": "kPa",
    "mpa": "MPa",
    "mbar": "mbar",
    "bar": "bar",
    "bara": "bar",
    "barg": "bar",
    "bar(g)": "bar",
    "psi": "psi",
    "psia": "psi",
    "psig": "psi",
    "mmhg": "mmHg",
    "torr": "Torr",
}

_TEMP_RE = re.compile(r"temp|°c", re.IGNORECASE)
_HUM_RE = re.compile(r"hum|feucht|(^|[^a-z])(hr|rh|rf)([^a-z]|$)", re.IGNORECASE)

# Grandeurs physiques reconnues
VOLTAGE = "tension"
CURRENT = "courant"
TEMPERATURE = "température"
HUMIDITY = "humidité"
PRESSURE = "pression"
OTHER = "autre"

_QUANTITY_BY_UNIT = {
    "µV": VOLTAGE,
    "mV": VOLTAGE,
    "V": VOLTAGE,
    "kV": VOLTAGE,
    "mA": CURRENT,
    "A": CURRENT,
    "µA": CURRENT,
    "kA": CURRENT,
    "°C": TEMPERATURE,
    "%HR": HUMIDITY,
    **{u: PRESSURE for u in ("Pa", "hPa", "kPa", "MPa", "mbar", "bar", "psi", "mmHg", "Torr")},
}

# Conversions d'unités (issue #20) : facteur vers l'unité de base de chaque famille. Pas de
# température (°C -> °F n'est pas un simple facteur, et les corrections °C en dépendent).
UNIT_FAMILIES = (
    {"µV": 1e-6, "mV": 1e-3, "V": 1.0, "kV": 1e3},
    {"µA": 1e-6, "mA": 1e-3, "A": 1.0, "kA": 1e3},
    {"Pa": 1.0, "hPa": 1e2, "kPa": 1e3, "MPa": 1e6, "mbar": 1e2, "bar": 1e5, "psi": 6894.757293168,
     "mmHg": 133.322387415, "Torr": 101325.0 / 760.0},
    {"mW": 1e-3, "W": 1.0, "kW": 1e3},
    {"mΩ": 1e-3, "Ω": 1.0, "kΩ": 1e3},
)


def compatible_units(unit: str) -> list:
    """Unités vers lesquelles une voie peut être convertie (la sienne comprise), ou [] si aucune."""
    for family in UNIT_FAMILIES:
        if unit in family:
            return list(family)
    return []


def conversion_factor(old: str, new: str) -> float:
    """Facteur à appliquer aux valeurs en ``old`` pour les exprimer en ``new`` (ex. A -> mA : 1000)."""
    for family in UNIT_FAMILIES:
        if old in family and new in family:
            return family[old] / family[new]
    raise ValueError("{} -> {}".format(old, new))


# Unité par défaut des voies "CHx" sans unité d'un Graphtec (jamais supposée ailleurs :
# une voie "Channel 2" de nanodac peut très bien être une température)
DEFAULT_CHANNEL_UNIT = "mV"


@dataclass(frozen=True)
class Channel:
    """Une voie de mesure d'un fichier."""

    raw_name: str  # libellé d'origine dans le fichier
    label: str  # libellé affiché (légende, liste, export) : « Channel 4 » pour une voie CHx
    unit: str  # unité normalisée ("" si inconnue)
    quantity: str  # grandeur physique (tension, courant, ...)
    alias: str = ""  # nom donné à la voie dans la centrale (ex. « I_s1 »), repris dans le rapport

    @property
    def is_climatic(self) -> bool:
        """Voie issue de l'enceinte climatique (température / humidité)."""
        return self.quantity in (TEMPERATURE, HUMIDITY)


# Écritures du degré Celsius et de l'humidité relative rencontrées dans les exports (issue #11) :
# « ℃ » (caractère unique des centrales japonaises), « Â°C » / « �C » (degré mal décodé),
# « ° C », « oC », « Deg.C », « % RH », « RH% »…
_CELSIUS_KEYS = {"°c", "c°", "degc", "degreec", "degreesc", "celsius", "oc", "°", "deg"}
_HUMIDITY_KEYS = {"%rh", "rh%", "%hr", "hr%", "rh", "hr", "%hrel", "%rel", "%rf", "rf%", "rf"}  # rF : Feuchte


def normalize_unit(unit: str) -> str:
    """Normalise une unité ("mv" -> "mV", "deg C" -> "°C", "℃" -> "°C"). Renvoie l'unité telle
    quelle si inconnue."""
    cleaned = unit.strip()
    key = re.sub(r"[\s.]", "", cleaned).lower()
    key = key.replace("\u2103", "°c").replace("â°", "°").replace("\ufffd", "°").replace("º", "°")
    if key in _CELSIUS_KEYS:
        return "°C"
    if key in _HUMIDITY_KEYS:
        return "%HR"
    return _UNIT_ALIASES.get(cleaned.lower(), cleaned)


def split_name_unit(raw: str) -> Tuple[str, str]:
    """Sépare "Tension (V)" en ("Tension", "V")."""
    text = raw.strip().strip('"').strip("'").strip()
    match = _UNIT_RE.search(text)
    if not match:
        return " ".join(text.split()), ""
    name = text[: match.start()].strip()
    return " ".join(name.split()), normalize_unit(match.group(1))


# Grandeur reconnue dans le nom de la voie quand l'unité n'est pas connue. « U_… » et
# « I_… » : notations usuelles de la tension et du courant (« U_supply », « I_EOM »).
_QUANTITY_BY_NAME = [
    (TEMPERATURE, re.compile(r"temp|celsius|°c", re.IGNORECASE)),
    (HUMIDITY, _HUM_RE),
    (VOLTAGE, re.compile(r"volt|tension|^u(_|\b|\d)", re.IGNORECASE)),
    (CURRENT, re.compile(r"courant|amp[eè]re|current|^i(_|\b|\d)", re.IGNORECASE)),
    (PRESSURE, re.compile(r"press|druck", re.IGNORECASE)),
]

# Nom affiché de chaque grandeur (axes du graphique) ; traduit à l'affichage
QUANTITY_NAMES = {VOLTAGE: "Tension", CURRENT: "Courant", TEMPERATURE: "Température", HUMIDITY: "Humidité",
                  PRESSURE: "Pression"}


def quantity_of(unit: str, *names: str) -> str:
    """Grandeur d'après l'unité, sinon d'après le nom de la voie ou du signal."""
    quantity = _QUANTITY_BY_UNIT.get(unit, OTHER)
    if quantity == OTHER and not unit:
        for candidate, pattern in _QUANTITY_BY_NAME:
            if any(name and pattern.search(name) for name in names):
                return candidate
    return quantity


def make_channel(
    raw: str, unit: Optional[str] = None, alias: str = "", default_unit: str = ""
) -> Channel:
    """Construit une voie avec un libellé lisible à partir du nom brut de la colonne.

    ``alias`` : nom donné à la voie dans la centrale (ex. « Signal name » Graphtec).
    ``default_unit`` : unité des voies « CHx » sans unité connue (mV pour un Graphtec).
    """
    name, found_unit = split_name_unit(raw)
    unit = normalize_unit(unit) if unit else found_unit

    alias = " ".join(alias.split())
    match = _CHANNEL_RE.match(name)
    if match:
        # Voie d'une centrale : libellé « Channel N » (comme sur l'appareil) ; le nom du
        # signal et l'unité restent connus (rapport, axes) mais ne surchargent pas la légende.
        unit = unit or default_unit
        if alias.lower() == raw.strip().lower():
            alias = ""
        label = "Channel {}".format(int(match.group(1)))
        return Channel(raw_name=raw, label=label, unit=unit, quantity=quantity_of(unit, alias), alias=alias)
    if not unit:
        if _TEMP_RE.search(name):
            unit = "°C"
        elif _HUM_RE.search(name):
            unit = "%HR"

    # "HR (%)" est une humidité relative, pas un pourcentage quelconque
    if unit == "%" and _HUM_RE.search(name):
        unit = "%HR"

    name = name or raw.strip() or "Voie"
    if alias and alias.lower() not in (raw.strip().lower(), name.lower()):
        name = "{} - {}".format(name, alias)
    label = "{} ({})".format(name, unit) if unit else name
    return Channel(raw_name=raw, label=label, unit=unit, quantity=quantity_of(unit, name, alias), alias=alias)
