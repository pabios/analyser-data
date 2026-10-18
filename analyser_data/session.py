"""État de travail de l'application : fichiers chargés, données brutes et nettoyées.

Cette classe ne dépend pas de Tkinter : toute la logique métier est testable sans IHM.
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from .cleaning import (
    CleaningOptions, CleaningReport, clean_signal, default_noise_threshold, estimate_rest_offset,
    has_rest_phase, suggest_noise_threshold,
)
from .editing import interpolate_at
from .export import Key, merge_selection, write_csv
from .loader import TIME_COL, LoadError, Measurement, align_time_axes, load_measurement
from .channels import HUMIDITY, TEMPERATURE, Channel
from .plotting import PlotSeries
from .i18n import _, get_language


# Bases de temps pour superposer plusieurs fichiers
TIME_MODES = OrderedDict([
    ("real", "Heure réelle"),  # chaque mesure à son heure (même essai, appareils à l'heure)
    ("common", "Période commune"),  # uniquement la période où tous les fichiers mesurent
    ("zero", "Débuts à 0"),  # chaque fichier démarre à 0 (horloges pas à l'heure)
    ("stretch", "Durée étirée (0-100 %)"),  # chaque fichier de 0 à 100 % de sa durée
])

# Grilles d'export proposées (None : celle du fichier de référence)
EXPORT_STEPS = OrderedDict([
    (None, "Grille du fichier de référence"),
    (0.1, "100 ms"),
    (1.0, "1 s"),
    (10.0, "10 s"),
    (60.0, "1 min"),
])


# Correction de température (issue #11) : température affichée = mesurée + correction
TEMPERATURE_OFFSET_RANGE = (-5.0, 5.0)  # °C
TEMPERATURE_OFFSET_STEP = 0.1


def parse_temperature_offset(text, unit: str = "°C") -> float:
    """« +0,5 » / « -1.2 » -> valeur arrondie au pas de 0,1. ValueError (message lisible) si le
    texte n'est pas un nombre ou sort de la plage autorisée (-5,0 à +5,0 °C ou %HR)."""
    low, high = TEMPERATURE_OFFSET_RANGE
    message = _("Correction invalide : « {} ». Saisissez une valeur entre {} et {} {} (pas de 0,1).").format(
        text, format_offset(low), format_offset(high), unit)
    try:
        value = float(str(text).strip().replace(",", ".").replace(unit, "").replace("°C", "").replace("+", "") or 0)
    except ValueError:
        raise ValueError(message) from None
    if not (math.isfinite(value) and low - 1e-9 <= value <= high + 1e-9):
        raise ValueError(message)
    # Arrondi décimal au dixième, 0,05 -> 0,1 (0,1 + 0,2 = 0,30000000000000004 -> 0,3) ; -0,0 -> 0,0
    return float(Decimal(repr(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) + 0.0


def format_offset(value: float) -> str:
    """+0,5 °C (virgule en français)."""
    text = "{:+.1f}".format(value)
    return text.replace(".", ",") if get_language() == "fr" else text


class Session:
    def __init__(self) -> None:
        self.measurements: "OrderedDict[str, Measurement]" = OrderedDict()
        self._raw: Dict[str, pd.DataFrame] = {}  # copie des données brutes, pour restaurer
        self.reference: Optional[Measurement] = None
        self.time_offset_min = 0.0  # décalage des courbes climatiques (minutes)
        # Voies qui suivent le curseur de décalage dans le temps (issue #13) ; None : toutes les °C
        self.shifted_keys: Optional[Set[Key]] = None
        self.time_mode = "real"
        self.window: Optional[Tuple[float, float]] = None  # période commune (minutes)
        self.export_step_s: Optional[float] = None
        self.noise_thresholds: Dict[Key, Optional[float]] = {}  # seuils de bruit réglés par voie
        self.offsets: Dict[Key, Optional[float]] = {}  # décalages de zéro réglés par voie
        # Corrections ajoutées aux voies °C / %HR (affichage et exports, issue #11) : valeur
        # générale par grandeur, ou valeur propre à une sonde (prioritaire)
        self.temperature_offset = 0.0
        self.humidity_offset = 0.0
        self.channel_offsets: Dict[Key, float] = {}
        self.journal: List[Tuple[pd.Timestamp, str]] = []  # traçabilité des traitements

    # ------------------------------------------------------------------ journal

    def log(self, text: str) -> None:
        """Ajoute une ligne au journal des traitements (joint aux exports et au rapport)."""
        self.journal.append((pd.Timestamp.now().floor("s"), text))

    def journal_text(self) -> str:
        from . import APP_NAME, __version__

        lines = [_("Journal des traitements — {} v{}").format(APP_NAME, __version__),
                 _("Édité le {}").format(pd.Timestamp.now().strftime("%d/%m/%Y %H:%M")), ""]
        lines += ["{}  {}".format(t.strftime("%d/%m/%Y %H:%M:%S"), text) for t, text in self.journal]
        return "\n".join(lines) + "\n"

    # ----------------------------------------------------------------- fichiers

    def load_files(
        self, paths: Iterable, progress: Optional[Callable[[str], None]] = None
    ) -> Tuple[List[Measurement], List[Tuple[Path, str]]]:
        """Charge plusieurs fichiers. Renvoie (fichiers chargés, [(chemin, message d'erreur)])."""
        loaded, errors = self.read_files(paths, progress)
        self.add_measurements(loaded)
        return loaded, errors

    def read_files(
        self, paths: Iterable, progress: Optional[Callable[[str], None]] = None,
        options: Optional[Dict[str, dict]] = None,
    ) -> Tuple[List[Measurement], List[Tuple[Path, str]]]:
        """Lit les fichiers sans modifier la session (peut tourner en arrière-plan).

        ``options[str(chemin)]`` : réglages d'import imposés (``sep``, ``sheet``).
        Renvoie (fichiers chargés, [(chemin, message d'erreur)]).
        """
        paths = list(paths)
        loaded, errors = [], []
        taken = set(self.measurements)
        for i, path in enumerate(paths, start=1):
            name = _unique_name(Path(path).name, taken)
            taken.add(name)
            prefix = _("Fichier {}/{} — ").format(i, len(paths)) if len(paths) > 1 else ""
            report = (lambda msg, p=prefix: progress(p + msg)) if progress else None
            try:
                loaded.append(load_measurement(path, name=name, progress=report,
                                               **(options or {}).get(str(path), {})))
            except LoadError as exc:
                errors.append((Path(path), str(exc)))
        return loaded, errors

    def add_measurements(self, measurements: Sequence[Measurement]) -> None:
        for m in measurements:
            self.measurements[m.name] = m
            self._raw[m.name] = m.data.copy()
            end = m.start + pd.Timedelta(minutes=m.duration_min) if m.start is not None else None
            self.log(_("Fichier ouvert : {} — {} · {} · {} points{} · voies : {}").format(
                m.name, m.source, m.period_label, len(m.data),
                _(" · du {} au {}").format(m.start.strftime("%d/%m/%Y %H:%M:%S"), end.strftime("%d/%m/%Y %H:%M:%S"))
                if end is not None else "",
                ", ".join(ch.label for ch in m.channels)))
            for text in self.corrections_summary([(m.name, ch.label) for ch in m.channels]):
                self.log(text)
        self._realign()

    def remove_file(self, name: str) -> None:
        self.measurements.pop(name, None)
        self._raw.pop(name, None)
        self._realign()

    def clear(self) -> None:
        """Réinitialisation complète : décharge tous les fichiers."""
        self.measurements.clear()
        self._raw.clear()
        self.reference = None
        self.time_offset_min = 0.0
        self.shifted_keys = None
        self.window = None
        self.noise_thresholds.clear()
        self.offsets.clear()
        self.journal.clear()

    def set_time_mode(self, mode: str) -> None:
        if mode not in TIME_MODES:
            raise ValueError(mode)
        self.time_mode = mode
        self._realign()
        self.log(_("Base de temps : {}").format(_(TIME_MODES[mode])))

    @property
    def percent_axis(self) -> bool:
        """Axe du temps en % de la durée (mode « étiré ») au lieu de minutes."""
        return self.time_mode == "stretch"

    @property
    def window_start(self) -> Optional[pd.Timestamp]:
        """Heure réelle du début de la période commune."""
        if self.window is None or self.reference is None or self.reference.start is None:
            return None
        return self.reference.start + pd.Timedelta(minutes=self.window[0])

    def all_keys(self) -> List[Key]:
        return [(m.name, ch.label) for m in self.measurements.values() for ch in m.channels]

    @property
    def total_duration_min(self) -> float:
        if not self.measurements:
            return 0.0
        return max(float(np.nanmax(m.data[TIME_COL])) for m in self.measurements.values()) - min(
            float(np.nanmin(m.data[TIME_COL])) for m in self.measurements.values()
        )

    # ---------------------------------------------------------------- traitements

    def noise_threshold(self, key: Key) -> Optional[float]:
        """Seuil de bruit de repos de la voie : réglé par l'utilisateur, sinon celui de l'unité.

        Par défaut, une voie qui ne revient jamais à 0 (petit courant permanent, tension
        d'alimentation...) n'est pas mise à 0 : le seuil de l'unité effacerait de vraies mesures.
        """
        if key in self.noise_thresholds:
            return self.noise_thresholds[key]
        if not self.has_rest_phase(key):
            return None
        # Seuil calculé sur les données (bruit réel des repos). Pas de seuil fixe de l'unité
        # par défaut : 10 mA effaceraient entièrement un petit courant réel de 0,02 mA.
        return self.suggest_noise_threshold(key)

    def has_rest_phase(self, key: Key) -> bool:
        return has_rest_phase(self.measurements[key[0]].data[key[1]].to_numpy(dtype=float))

    def suggest_noise_threshold(self, key: Key) -> Optional[float]:
        return suggest_noise_threshold(self.measurements[key[0]].data[key[1]].to_numpy(dtype=float))

    def zero_offset(self, key: Key) -> Optional[float]:
        """Décalage de zéro de la voie : réglé par l'utilisateur, sinon celui mesuré au repos."""
        if key in self.offsets:
            return self.offsets[key]
        return self.suggest_offset(key)

    def suggest_offset(self, key: Key) -> Optional[float]:
        return estimate_rest_offset(self.measurements[key[0]].data[key[1]].to_numpy(dtype=float))

    def preview_cleaning(
        self, keys: Sequence[Key], options: CleaningOptions,
        thresholds: Optional[Dict[Key, Optional[float]]] = None,
        offsets: Optional[Dict[Key, Optional[float]]] = None,
        progress: Optional[Callable] = None,
    ) -> Dict[Key, CleaningReport]:
        """Nombre de points que le nettoyage modifierait, voie par voie (sans rien modifier)."""
        out = {}
        for i, key in enumerate(keys):
            if progress:
                progress(_("Aperçu — voie {}/{} : {}").format(i + 1, len(keys), key[1]), i / len(keys))
            m = self.measurements[key[0]]
            threshold = (thresholds or {}).get(key, self.noise_threshold(key))
            offset = (offsets or {}).get(key, self.zero_offset(key))
            _cleaned, out[key] = clean_signal(m.data[key[1]].to_numpy(dtype=float), m.channel(key[1]).unit,
                                       options, noise_threshold=threshold, offset=offset)
        return out

    def apply_cleaning(
        self, keys: Sequence[Key], options: CleaningOptions,
        thresholds: Optional[Dict[Key, Optional[float]]] = None,
        offsets: Optional[Dict[Key, Optional[float]]] = None,
        progress: Optional[Callable] = None,
    ) -> CleaningReport:
        """US-03 : nettoie les voies. Seuils et décalages par voie mémorisés, opérations journalisées."""
        if thresholds:
            self.noise_thresholds.update(thresholds)
        if offsets:
            self.offsets.update(offsets)
        total = CleaningReport()
        for i, key in enumerate(keys):
            name, label = key
            if progress:
                progress(_("Nettoyage — voie {}/{} : {}").format(i + 1, len(keys), label), i / len(keys))
            m = self.measurements[name]
            ch = m.channel(label)
            threshold, offset = self.noise_threshold(key), self.zero_offset(key)
            cleaned, report = clean_signal(m.data[label].to_numpy(dtype=float), ch.unit, options,
                                           noise_threshold=threshold, offset=offset)
            m.data[label] = cleaned
            total.noise_points += report.noise_points
            total.peak_points += report.peak_points
            total.smoothed_points += report.smoothed_points
            done = []
            if report.offset:
                done.append(_("décalage de zéro {:+.4g} {} soustrait").format(report.offset, ch.unit))
            if options.remove_peaks:
                done.append(_("{} point(s) de pics parasites corrigés ({}, ≤ {} points)").format(
                    report.peak_points, _("saturation > {:.0%} du max").format(options.saturation_ratio)
                    if options.peak_mode == "saturation" else _("tous les pics étroits"), options.max_peak_width))
            if options.remove_noise:
                done.append(_("bruit lissé sans changer le niveau (fenêtre {} points, fronts conservés)").format(
                    options.smooth_window))
            if options.zero_rest:
                done.append(_("{} point(s) de bruit de repos mis à 0 (seuil {})").format(
                    report.noise_points, _("{:g} {}").format(threshold, ch.unit) if threshold else _("aucun")))
            self.log(_("Nettoyage {} [{}] : {}").format(label, name, " ; ".join(done) or _("rien")))
        return total

    def restore_raw(self, keys: Sequence[Key]) -> None:
        """Annule nettoyages et corrections des voies données."""
        for name, label in keys:
            self.measurements[name].data[label] = self._raw[name][label].to_numpy(copy=True)
            self.log(_("Données brutes restaurées : {} [{}]").format(label, name))

    def modified_points(self, key: Key) -> int:
        """Nombre de points qui diffèrent des données brutes (nettoyage + corrections)."""
        name, label = key
        now = self.measurements[name].data[label].to_numpy(dtype=float)
        raw = self._raw[name][label].to_numpy(dtype=float)
        return int(np.sum(~((now == raw) | (np.isnan(now) & np.isnan(raw)))))

    def raw_values(self, key: Key) -> np.ndarray:
        return self._raw[key[0]][key[1]].to_numpy(dtype=float)

    def correct_point(self, key: Key, index: int) -> float:
        """US-05 : remplace le point ``index`` par l'interpolation de ses voisins."""
        name, label = key
        df = self.measurements[name].data
        old = float(df[label].iloc[index])
        value = interpolate_at(df[TIME_COL].to_numpy(), df[label].to_numpy(), index)
        self.set_value(key, index, value)
        m = self.measurements[name]
        when = (m.start + pd.Timedelta(minutes=float(m.elapsed_min[index]))).strftime("%d/%m/%Y %H:%M:%S.%f")[:-5] \
            if m.start is not None else format_hms_safe(float(m.elapsed_min[index]))
        self.log(_("Correction au clic : {} [{}] point n°{} ({}) : {:.6g} -> {:.6g}").format(
            label, name, index + 1, when, old, value))
        return value

    def set_value(self, key: Key, index: int, value: float) -> None:
        """Correction manuelle d'un point (US-05)."""
        name, label = key
        df = self.measurements[name].data
        df.iloc[index, df.columns.get_loc(label)] = value

    # ---------------------------------------------------------------- affichage

    def series(self, keys: Sequence[Key], with_raw: bool = False) -> List[PlotSeries]:
        """Courbes à tracer. ``with_raw`` : ajoute, sous chaque voie modifiée, ses données brutes."""
        out = []
        temperatures = [k for k in keys if self.measurements[k[0]].channel(k[1]).quantity == TEMPERATURE]
        # Rang fixe de chaque voie électrique parmi toutes les voies chargées : sa couleur ne
        # change pas quand on coche / décoche les autres (issue #4)
        rank = {(m.name, c.label): i for i, (m, c) in enumerate(
            (m, c) for m in self.measurements.values() for c in m.channels if not c.is_climatic)}
        for i, (name, label) in enumerate(keys):
            m = self.measurements[name]
            ch = m.channel(label)
            x = m.data[TIME_COL].to_numpy(dtype=float)
            y = self.displayed_values((name, label))
            first = 0
            if self.window is not None:  # période commune : on ne garde que la fenêtre
                first = int(np.searchsorted(x, self.window[0] - 1e-9))
                last = int(np.searchsorted(x, self.window[1] + 1e-9, side="right"))
                x, y = x[first:last], y[first:last]
            out.append(
                PlotSeries(
                    gid=str(i),
                    label=self._legend_with_correction(ch, (name, label), temperatures.index((name, label)) + 1
                                                       if len(temperatures) > 1 and (name, label) in temperatures
                                                       else 0),
                    x=x,
                    y=y,
                    unit=ch.unit,
                    quantity=ch.quantity,
                    shiftable=self.is_key_shifted((name, label)) and not self.percent_axis,
                    index_base=first,
                    key=(name, label),
                    color_index=rank.get((name, label), -1),
                    color_total=len(rank),
                )
            )
            if with_raw and self.modified_points((name, label)):
                raw = self.raw_values((name, label))[first:first + len(x)] + self.value_offset((name, label))
                out.append(PlotSeries(gid=_("raw:{}").format(i), label=_("_brut"), x=x, y=raw, unit=ch.unit,
                                      quantity=ch.quantity, shiftable=out[-1].shiftable, index_base=first, raw=True,
                                      key=(name, label)))
        return out

    # ------------------------------------------------------- décalage dans le temps

    def is_key_shifted(self, key: Key) -> bool:
        """La voie suit-elle le curseur de décalage dans le temps ? (issue #13) Par défaut :
        les voies °C, quel que soit leur fichier ; sinon les voies choisies par l'utilisateur."""
        if self.shifted_keys is not None:
            return key in self.shifted_keys
        return self.measurements[key[0]].channel(key[1]).quantity == TEMPERATURE

    def shifted_channels(self) -> List[Key]:
        return [(m.name, c.label) for m in self.measurements.values() for c in m.channels
                if self.is_key_shifted((m.name, c.label))]

    def set_shifted_keys(self, keys: Optional[Iterable[Key]]) -> None:
        """Voies décalées choisies à la main (None : toutes les voies °C)."""
        keys = None if keys is None else set(keys)
        if keys == self.shifted_keys:
            return
        self.shifted_keys = keys
        if self.measurements:
            self.log(_("Décalage dans le temps appliqué à : {}").format(
                ", ".join("{} [{}]".format(label, name) for name, label in self.shifted_channels())
                or _("aucune voie")))

    # ------------------------------------------------------- correction de température

    def set_temperature_offset(self, value: float) -> None:
        """Correction générale ajoutée aux voies °C à l'affichage et aux exports ; les données
        mesurées ne sont pas modifiées (la correction peut être changée ou retirée)."""
        value = parse_temperature_offset(value)
        if value != self.temperature_offset:
            self.temperature_offset = value
            self._log_correction(_("toutes les voies °C"), value, "°C")

    def set_humidity_offset(self, value: float) -> None:
        """Correction générale ajoutée aux voies %HR."""
        value = parse_temperature_offset(value, "%HR")
        if value != self.humidity_offset:
            self.humidity_offset = value
            self._log_correction(_("toutes les voies %HR"), value, "%HR")

    def set_channel_offset(self, key: Key, value: Optional[float]) -> None:
        """Correction propre à une sonde (prioritaire sur la valeur générale) ; None : la retire."""
        unit = self.measurements[key[0]].channel(key[1]).unit if key[0] in self.measurements else "°C"
        if value is None:
            if self.channel_offsets.pop(key, None) is not None and key[0] in self.measurements:
                self.log(_("Correction propre à {} [{}] retirée : valeur générale").format(key[1], key[0]))
            return
        value = parse_temperature_offset(value, unit)
        if self.channel_offsets.get(key) != value:
            self.channel_offsets[key] = value
            if key[0] in self.measurements:
                self._log_correction("{} [{}]".format(key[1], key[0]), value, unit)

    def _log_correction(self, target: str, value: float, unit: str) -> None:
        if self.measurements:
            self.log(_("Correction {} : {} {} (valeur affichée = mesurée + correction)").format(
                target, format_offset(value), unit))

    def value_offset(self, key: Key) -> float:
        """Correction à ajouter aux valeurs de la voie (température et humidité seulement)."""
        ch = self.measurements[key[0]].channel(key[1])
        if ch.quantity not in (TEMPERATURE, HUMIDITY):
            return 0.0
        if key in self.channel_offsets:
            return self.channel_offsets[key]
        return self.temperature_offset if ch.quantity == TEMPERATURE else self.humidity_offset

    def _legend_with_correction(self, ch: Channel, key: Key, number: int) -> str:
        """« Température ambiante (+0,5 °C) », « Channel 4 (-1,2 %HR) » : correction visible."""
        label = legend_label(ch, number)
        offset = self.value_offset(key)
        if not offset:
            return label
        suffix = " ({})".format(ch.unit)
        if label.endswith(suffix):
            label = label[: -len(suffix)]
        return "{} ({} {})".format(label, format_offset(offset), ch.unit)

    def corrections_summary(self, keys: Optional[Sequence[Key]] = None) -> List[str]:
        """Corrections actives sur ces voies (toutes par défaut), pour le journal et le rapport."""
        keys = keys if keys is not None else [(m.name, c.label) for m in self.measurements.values()
                                              for c in m.channels]
        lines = []
        for key in keys:
            offset = self.value_offset(key)
            if offset:
                ch = self.measurements[key[0]].channel(key[1])
                lines.append(_("Correction {} [{}] : {} {} (valeur affichée = mesurée + correction)").format(
                    key[1], key[0], format_offset(offset), ch.unit))
        return lines

    def displayed_values(self, key: Key) -> np.ndarray:
        """Valeurs telles qu'affichées et exportées (correction de température comprise)."""
        return self.measurements[key[0]].data[key[1]].to_numpy(dtype=float) + self.value_offset(key)

    # ------------------------------------------------------------------- export

    def export_csv(self, path, keys: Sequence[Key], progress: Optional[Callable] = None) -> pd.DataFrame:
        """US-06 : CSV des voies + journal des traitements à côté (« …_journal.txt »)."""
        if self.reference is None:
            raise ValueError(_("Aucun fichier chargé."))
        if progress:
            progress(_("Fusion de {} voie(s) sur une base de temps commune…").format(len(keys)), 0.1)
        df = merge_selection(
            list(self.measurements.values()), self.reference, keys, self.time_offset_min,
            window=self.window, step_s=self.export_step_s, percent=self.percent_axis,
            value_offsets={key: self.value_offset(key) for key in keys},
            shifted_keys=set(self.shifted_channels()),
        )
        if progress:
            progress(_("Écriture de {} lignes dans {}…").format(len(df), Path(path).name), 0.5)
        write_csv(df, path)
        self.log(_("Export CSV : {} ({} lignes × {} voies, base de temps : {}, grille : {})").format(
            Path(path).name, len(df), len(keys), _(TIME_MODES[self.time_mode]), _(EXPORT_STEPS[self.export_step_s]))
            + (_(" ; corrections °C / %HR incluses") if any(self.value_offset(k) for k in keys) else ""))
        journal_path(path).write_text(self.journal_text(), encoding="utf-8")
        return df

    # --------------------------------------------------------------- interne

    def _realign(self) -> None:
        measurements = list(self.measurements.values())
        self.reference = align_time_axes(measurements)  # heure réelle
        self.window = None
        if self.time_mode == "zero":
            for m in measurements:
                m.data[TIME_COL] = m.elapsed_min
                m.align_note = ""
        elif self.time_mode == "stretch":
            for m in measurements:
                m.data[TIME_COL] = m.elapsed_min / max(m.duration_min, 1e-9) * 100.0
                m.align_note = ""
        elif self.time_mode == "common":
            self.window = self._common_window(measurements)
        for name, m in self.measurements.items():
            self._raw[name][TIME_COL] = m.data[TIME_COL].to_numpy(copy=True)


    @staticmethod
    def _common_window(measurements) -> Optional[Tuple[float, float]]:
        """Période où tous les fichiers recalés sur l'heure réelle mesurent en même temps."""
        aligned = [m for m in measurements if not m.align_note and len(m.data)]
        if len(aligned) < 2:
            return None
        start = max(float(m.data[TIME_COL].iloc[0]) for m in aligned)
        end = min(float(m.data[TIME_COL].iloc[-1]) for m in aligned)
        return (start, end) if end > start else None


def legend_label(ch: Channel, temperature_number: int = 0) -> str:
    """Nom d'une voie dans la légende (issues #1 et #3) : « Channel 1 (V) », jamais le nom du
    fichier ; la température est la « Température ambiante » (numérotée s'il y en a plusieurs)."""
    if ch.quantity == TEMPERATURE:
        if temperature_number:
            return _("Température ambiante {}").format(temperature_number)
        return _("Température ambiante")
    if ch.unit and ch.label.startswith("Channel "):
        return "{} ({})".format(ch.label, ch.unit)
    return ch.label


def journal_path(path) -> Path:
    """Fichier journal joint à un export : « essai.csv » -> « essai_journal.txt »."""
    path = Path(path)
    return path.with_name(path.stem + "_journal.txt")


def format_hms_safe(minutes: float) -> str:
    from .plotting import format_hms

    return format_hms(minutes)


def _unique_name(name: str, taken) -> str:
    if name not in taken:
        return name
    i = 2
    while "{} ({})".format(name, i) in taken:
        i += 1
    return "{} ({})".format(name, i)
