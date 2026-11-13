"""US-04 — Visualisation multi-axes : V / A à gauche, °C et %HR sur des axes secondaires.

* axes Y secondaires (``twinx``) créés automatiquement pour la température et l'humidité ;
* axe X au format ``H:MM:SS`` ;
* décalage temporel réglable des courbes climatiques (retard du banc thermique) ;
* légende multi-colonnes au-delà de 20 voies.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from matplotlib.ticker import FuncFormatter, Locator, MaxNLocator

from .channels import HUMIDITY, TEMPERATURE
from .i18n import _

# Position de la légende, toujours HORS de la zone des courbes (issue #6)
LEGEND_POSITIONS = {"right": "À droite", "top": "En haut", "bottom": "En bas", "hidden": "Masquée"}
_LEGEND_LOC = {"right": "outside right upper", "top": "outside upper center", "bottom": "outside lower center"}


@dataclass
class PlotSeries:
    gid: str  # identifiant de la courbe (sert à la correction au clic)
    label: str  # texte de la légende
    x: np.ndarray  # Time_min
    y: np.ndarray
    unit: str
    quantity: str
    shiftable: bool = False  # courbe climatique concernée par le décalage temporel
    index_base: int = 0  # indice, dans les données complètes, du premier point de x / y
    raw: bool = False  # données brutes affichées en gris sous la voie nettoyée (comparaison)
    key: Tuple[str, str] = ("", "")  # (fichier, voie) : sert aux réglages des courbes
    color_index: int = -1  # rang fixe de la voie parmi toutes les voies chargées (couleur stable)
    color_total: int = 0


MAIN_AXIS = "main"  # axe Y de gauche (signaux électriques) ; les autres : TEMPERATURE, HUMIDITY
LINE_STYLES = {"-": "Continu", "--": "Tirets", ":": "Pointillés", "-.": "Tiret-point"}
MARKERS = {"": "Aucun", ".": "Point", "o": "Rond", "s": "Carré", "x": "Croix"}


@dataclass
class AxisSettings:
    """Réglages d'un axe. None : automatique (limites calculées, libellé par défaut)."""
    min: Optional[float] = None
    max: Optional[float] = None
    label: Optional[str] = None
    log: bool = False
    step: Optional[float] = None  # pas entre deux graduations (issue #17) ; axe X en minutes


@dataclass
class CurveStyle:
    """Apparence d'une voie. None : valeur par défaut (couleur de la palette, nom de la voie)."""
    name: Optional[str] = None
    color: Optional[str] = None
    width: float = 1.2
    style: str = "-"
    marker: str = ""
    absolute: bool = False  # affichage en valeur absolue |x| (les données ne changent pas)


@dataclass
class PlotSettings:
    """Réglages du graphique (bouton « Modifier les axes et les courbes ») : gardés quand on
    change de voies ou qu'on nettoie, repris dans l'image et le rapport PDF."""
    title: str = ""
    x: AxisSettings = field(default_factory=AxisSettings)
    y: Dict[str, AxisSettings] = field(default_factory=dict)
    curves: Dict[Tuple[str, str], CurveStyle] = field(default_factory=dict)
    legend: str = "right"  # LEGEND_POSITIONS
    x_percent_step: Optional[float] = None  # pas de l'axe X en % (base de temps « Durée étirée »)

    def tick_steps(self) -> Dict[str, float]:
        """Pas de graduation choisis (issue #17), à mémoriser dans la configuration."""
        out = {"x": self.x.step, "x_percent": self.x_percent_step}
        out.update({name: axis.step for name, axis in self.y.items()})
        return {k: v for k, v in out.items() if v}

    def set_tick_steps(self, steps: Dict[str, float]) -> None:
        for name, value in (steps or {}).items():
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(value) and value > 0):
                continue
            if name == "x":
                self.x.step = value
            elif name == "x_percent":
                self.x_percent_step = value
            else:
                self.y_axis(name).step = value

    def y_axis(self, name: str) -> AxisSettings:
        return self.y.setdefault(name, AxisSettings())

    def clear_limits(self) -> None:
        """Retour aux limites automatiques (bouton « Maison »), libellés et styles gardés."""
        for axis in [self.x] + list(self.y.values()):
            axis.min = axis.max = None


def parse_hms(text: str) -> Optional[float]:
    """« H:MM:SS » ou « H:MM » (heures au-delà de 24 acceptées, signe « - » possible) -> minutes.

    Champ vide : None (automatique). ValueError si le texte n'est pas lisible.
    """
    text = text.strip()
    if not text:
        return None
    sign = -1.0 if text.startswith("-") else 1.0
    parts = text.lstrip("+-").replace(",", ".").split(":")
    if not 2 <= len(parts) <= 3 or not all(p.strip() for p in parts):
        raise ValueError(text)
    hours, minutes = int(parts[0]), int(parts[1])
    seconds = float(parts[2]) if len(parts) == 3 else 0.0
    if not (0 <= minutes < 60 and 0 <= seconds < 60) or hours < 0:
        raise ValueError(text)
    return sign * (hours * 60 + minutes + seconds / 60.0)


def format_hms(minutes: float, _pos=None) -> str:
    """Minutes -> "H:MM:SS"."""
    if minutes is None or not math.isfinite(minutes):
        return ""
    total = int(round(minutes * 60))
    sign = "-" if total < 0 else ""
    hours, rest = divmod(abs(total), 3600)
    mins, secs = divmod(rest, 60)
    return _("{}{}:{:02d}:{:02d}").format(sign, hours, mins, secs)


def format_axis_time(minutes: float, _pos=None) -> str:
    """Graduation de l'axe X : toujours « H:MM:SS », même au-delà de 24 h (ex. « 48:00:00 »)."""
    return format_hms(minutes)


# Pas de graduation proposés (issue #17) ; None : automatique. Axe du temps : en minutes.
# Pas en minutes ; de 1 s à 15 min pour les essais courts (issue #16, demande du 2026-11-13)
TIME_TICK_STEPS = {None: "Automatique", 1 / 60: "1 s", 5 / 60: "5 s", 10 / 60: "10 s", 0.5: "30 s", 1.0: "1 min",
                   2.0: "2 min", 5.0: "5 min", 10.0: "10 min", 15.0: "15 min", 30.0: "30 min", 60.0: "1 h", 120.0: "2 h", 360.0: "6 h", 720.0: "12 h",
                   1440.0: "1 jour"}
VALUE_TICK_STEPS = {None: "Automatique", 0.1: "0,1", 0.5: "0,5", 1.0: "1", 2.0: "2", 5.0: "5", 10.0: "10",
                    20.0: "20", 50.0: "50", 100.0: "100"}
MAX_TICKS = 100  # au-delà (pas trop fin pour la période affichée), on prend un multiple du pas


def parse_tick_step(text: str, time_axis: bool) -> Optional[float]:
    """Pas de graduation saisi -> valeur (minutes pour l'axe du temps), None : automatique.

    Axe du temps : un choix de la liste (« 1 h », « 1 jour »), une durée « H:MM:SS » / « H:MM »,
    ou un nombre suivi de s, min, h ou j (« 15 min », « 3 h », « 2 j »). Autres axes : un
    nombre (virgule ou point). ValueError (message lisible) si illisible ou négatif."""
    text = (text or "").strip()
    choices = TIME_TICK_STEPS if time_axis else VALUE_TICK_STEPS
    for value, label in choices.items():
        if text in (label, _(label)):
            return value
    if not text:
        return None
    message = (_("Pas de graduation invalide : « {} » (ex. 10 s, 5 min, 1 h, 1:30:00, 1 jour).") if time_axis
               else _("Pas de graduation invalide : « {} » (ex. 5 ou 0,5).")).format(text)
    value = None
    if time_axis:
        if ":" in text:
            try:
                value = parse_hms(text)
            except ValueError:
                raise ValueError(message) from None
        else:
            import re

            match = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*(s|sec|secondes?|min|minutes?|h|heures?|hours?|j|jours?|"
                             r"d|days?)?\s*$", text, re.IGNORECASE)
            if not match:
                raise ValueError(message)
            number = float(match.group(1).replace(",", "."))
            unit = (match.group(2) or "min").lower()
            factor = 1 / 60 if unit.startswith("s") else 60.0 if unit.startswith("h") else \
                1440.0 if unit[0] in "jd" else 1.0
            value = number * factor
    else:
        try:
            value = float(text.replace(",", ".").replace(" ", ""))
        except ValueError:
            raise ValueError(message) from None
    if value is None or not math.isfinite(value) or value <= 0:
        raise ValueError(message)
    if time_axis and value < 1 / 60 - 1e-9:  # graduations en H:MM:SS : moins d'1 s, étiquettes identiques
        raise ValueError(_("Pas de graduation trop petit : « {} » (1 s au minimum).").format(text))
    return value


def format_tick_step(value: Optional[float], time_axis: bool) -> str:
    """Pas de graduation -> texte de la liste (« 1 h », « 0,5 », « Automatique »)."""
    choices = TIME_TICK_STEPS if time_axis else VALUE_TICK_STEPS
    if value in choices:
        return _(choices[value])
    if time_axis:
        return format_hms(value)
    text = "{:g}".format(value)
    from .i18n import get_language

    return text.replace(".", ",") if get_language() == "fr" else text


class StepLocator(Locator):
    """Graduations à un pas choisi par l'utilisateur, alignées sur ses multiples (0, 1 h, 2 h…).

    Si le pas donnerait des étiquettes qui se chevauchent (pas fin sur un long essai, ex.
    10 s sur 9 min), les étiquettes passent à un multiple « rond » du pas (×2, ×3, ×6…) ;
    ``minor=True`` : graduations sans étiquette au pas exact, pour garder le pas choisi
    visible. En zoomant, le pas choisi revient sur les étiquettes."""

    def __init__(self, step: float, minor: bool = False, label_chars: int = 8, time: bool = False):
        self.step = float(step)
        self.minor = minor
        self.label_chars = label_chars  # « 48:00:00 » : largeur d'une étiquette de l'axe du temps
        self.time = time  # axe du temps (minutes) : multiples en durées rondes (20 s, 1 min, 5 min…)

    def __call__(self):
        vmin, vmax = self.axis.get_view_interval()
        return self.tick_values(vmin, vmax, MAX_TICKS * 5 if self.minor else self._room())

    def _room(self) -> int:
        """Nombre d'étiquettes qui tiennent sur l'axe sans se chevaucher."""
        try:
            box = self.axis.axes.get_window_extent()
            # taille des étiquettes sans les créer (get_ticklabels rappellerait ce localisateur)
            from matplotlib import rcParams
            from matplotlib.font_manager import FontProperties

            size = self.axis._major_tick_kw.get("labelsize") or rcParams["{}tick.labelsize".format(self.axis.axis_name)]
            px = self.axis.figure.dpi / 72.0 * FontProperties(size=size).get_size_in_points()
            if self.axis.axis_name == "x":
                length, per_label = box.width, px * (0.62 * self.label_chars + 1.5)
            else:
                length, per_label = box.height, px * 2.0
            return max(2, min(MAX_TICKS, int(length / per_label)))
        except Exception:  # pas encore de figure dessinée
            return MAX_TICKS

    def tick_values(self, vmin, vmax, limit: int = MAX_TICKS):
        if vmax < vmin:
            vmin, vmax = vmax, vmin
        step = self.step
        count = (vmax - vmin) / step
        if count > limit:
            need = count / limit
            nice = [d for d in _NICE_MINUTES if d >= step * need - 1e-9
                    and abs(d / step - round(d / step)) < 1e-6] if self.time else []
            step = nice[0] if nice else step * (next((k for k in _NICE_FACTORS if k >= need), None) or math.ceil(need))
        start = math.ceil(vmin / step - 1e-9) * step
        ticks = np.arange(start, vmax + step * 1e-6, step)
        return np.round(np.round(ticks / self.step) * self.step, 12)  # pas de 0,30000000000000004


# Multiples « ronds » du pas choisi quand ses étiquettes ne tiennent pas : 1-2-5 pour les valeurs,
# durées usuelles pour le temps (10 s -> 20 s, 30 s, 1 min, 2 min, 5 min…)
_NICE_FACTORS = (2, 5, 10, 20, 50, 100, 200, 500, 1000)
_NICE_MINUTES = tuple(x / 60 for x in (1, 2, 5, 10, 15, 20, 30)) + (1, 2, 5, 10, 15, 20, 30, 60, 120, 180, 360, 720) \
    + tuple(1440 * d for d in (1, 2, 5, 7, 10, 14, 30, 60, 90, 180, 365))


class TimeLocator(Locator):
    """Graduations à des pas de temps « ronds » (10 s, 1 min, 15 min, 1 h, 6 h, 1 j...)."""

    STEPS_MIN = [1 / 60, 2 / 60, 5 / 60, 10 / 60, 15 / 60, 30 / 60, 1, 2, 5, 10, 15, 30,
                 60, 120, 180, 360, 720, 1440, 2880, 7 * 1440, 14 * 1440, 30 * 1440, 90 * 1440]

    def __init__(self, max_ticks: int = 8):
        self.max_ticks = max_ticks

    def __call__(self):
        vmin, vmax = self.axis.get_view_interval()
        return self.tick_values(vmin, vmax)

    def tick_values(self, vmin, vmax):
        if vmax < vmin:
            vmin, vmax = vmax, vmin
        span = max(vmax - vmin, 1e-9)
        step = next((s for s in self.STEPS_MIN if span / s <= self.max_ticks), self.STEPS_MIN[-1])
        start = math.ceil(vmin / step) * step
        return np.arange(start, vmax + step * 1e-6, step)


def _series_color(index: int, total: int) -> str:
    """Couleur de la voie de rang ``index`` : fixe, elle ne dépend pas des voies cochées (issue #4)."""
    from .theme import SERIES_COLORS

    return SERIES_COLORS[index % len(SERIES_COLORS)]


def _default_dash(index: int) -> str:
    """Trait par défaut : continu, puis tirets / pointillés quand la palette est épuisée."""
    from .theme import SERIES_COLORS

    return ("-", "--", ":", "-.")[(index // len(SERIES_COLORS)) % 4]


def _default_colors(series: Sequence[PlotSeries]) -> Dict[str, str]:
    """Température : rouge réservé (nuances si plusieurs) ; humidité : vert d'eau ; autres
    voies : couleur fixe d'après leur rang parmi toutes les voies chargées."""
    from .theme import HUMIDITY_SHADES, TEMPERATURE_SHADES

    out, counters = {}, {TEMPERATURE: 0, HUMIDITY: 0}
    others = [s for s in series if s.quantity not in counters]
    for s in series:
        if s.quantity in counters:
            shades = TEMPERATURE_SHADES if s.quantity == TEMPERATURE else HUMIDITY_SHADES
            out[s.gid] = shades[counters[s.quantity] % len(shades)]
            counters[s.quantity] += 1
        elif s.color_index >= 0:
            out[s.gid] = _series_color(s.color_index, s.color_total)
        else:
            out[s.gid] = _series_color(others.index(s), len(others))
    return out


# Au-delà de ce nombre de points visibles, une courbe est réduite pour l'affichage
# (min et max de chaque tranche : aucun pic ne disparaît). Les données ne sont jamais
# modifiées ; le détail complet réapparaît en zoomant.
MAX_BUCKETS = 2000


def axis_title(series: Sequence[PlotSeries]) -> str:
    """Titre d'un axe Y d'après les grandeurs tracées (issue #1) : « Tension (V) / Courant (mA) ».
    Grandeur inconnue : son unité seule ; rien de connu : « Signaux électriques »."""
    from .channels import QUANTITY_NAMES

    groups: Dict[str, List[str]] = {}
    for s in series:
        units = groups.setdefault(s.quantity, [])
        if s.unit and s.unit not in units:
            units.append(s.unit)
    parts = []
    for quantity, units in groups.items():
        name = _(QUANTITY_NAMES[quantity]) if quantity in QUANTITY_NAMES else ""
        unit_text = ", ".join(units)
        if name:
            parts.append("{} ({})".format(name, unit_text) if unit_text else name)
        elif unit_text:
            parts.append(unit_text)
    return " / ".join(parts) or _("Signaux électriques")


def _auto_layout(fig) -> None:
    """Mise en page recalculée à CHAQUE dessin : marges justes même si la taille ou l'échelle
    d'affichage (Windows à 125 %, 150 %) changent après le dessin, ex. au changement de langue."""
    # « constrained » : réserve aussi la place de la légende placée hors des courbes
    try:
        fig.set_layout_engine("constrained", w_pad=0.06, h_pad=0.06)
    except AttributeError:  # pragma: no cover - Matplotlib < 3.6
        fig.set_tight_layout(True)


def decimate_indices(y: np.ndarray, start: int, stop: int, buckets: int = MAX_BUCKETS) -> np.ndarray:
    """Indices à tracer entre ``start`` et ``stop`` : tous si peu nombreux, sinon min/max par tranche."""
    count = stop - start
    if count <= 2 * buckets:
        return np.arange(start, stop)
    size = count // buckets
    used = size * buckets
    seg = y[start:start + used].reshape(buckets, size)
    nan = np.isnan(seg)
    lo = np.where(nan, np.inf, seg).argmin(axis=1)
    hi = np.where(nan, -np.inf, seg).argmax(axis=1)
    base = start + np.arange(buckets) * size
    idx = np.empty(2 * buckets, dtype=np.int64)
    idx[0::2] = base + np.minimum(lo, hi)
    idx[1::2] = base + np.maximum(lo, hi)
    # Reste après la dernière tranche complète (jusqu'à « buckets » points) : il était oublié,
    # d'où un trait droit jusqu'au dernier point en fin d'essai (issue #16, essai de 73 h)
    rest = np.zeros(0, dtype=np.int64)
    if start + used < stop:  # découpé en tranches de même taille que les autres
        tail = y[start + used:stop]
        pad = (-len(tail)) % size
        block = np.concatenate([tail, np.full(pad, np.nan)]).reshape(-1, size)
        empty = np.isnan(block)
        t_lo = np.where(empty, np.inf, block).argmin(axis=1)
        t_hi = np.where(empty, -np.inf, block).argmax(axis=1)
        t_base = start + used + np.arange(len(block)) * size
        rest = np.concatenate([t_base + t_lo, t_base + t_hi])
        rest = rest[rest < stop]
    return np.unique(np.concatenate(([start], idx, rest, [stop - 1]))).astype(np.int64)


class _Trace:
    """Une courbe affichée : données complètes + indices actuellement tracés."""

    def __init__(self, line, x, y, shiftable, index_base=0, absolute=False):
        self.index_base = index_base
        self.absolute = absolute
        self.line = line
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        self.shiftable = shiftable
        self.index = np.arange(0)  # indice d'origine de chaque point tracé (-1 : coupure)
        self.gaps = _gaps(self.x)


def _gaps(x: np.ndarray) -> np.ndarray:
    """Indices i après lesquels l'enregistrement s'interrompt (x[i+1] - x[i] > 10 périodes) :
    la courbe y est coupée au lieu d'un long trait droit trompeur (issue #16)."""
    if len(x) < 3:
        return np.zeros(0, dtype=np.int64)
    diffs = np.diff(x)
    positive = diffs[diffs > 0]
    if not len(positive):
        return np.zeros(0, dtype=np.int64)
    return np.flatnonzero(diffs > 10 * float(np.median(positive)))


class PlotManager:
    """Dessine les séries sélectionnées sur une figure Matplotlib (sans pyplot)."""

    def __init__(self, figure):
        self.figure = figure
        self.time_offset_min = 0.0
        self.percent_axis = False  # axe du temps en % de la durée (mode « étiré »)
        self.legend_pad = 0.5  # écart légende / bord de la figure (en taille de police)
        self._traces: Dict[str, _Trace] = {}
        self._main_ax = None
        self.colors: Dict[str, str] = {}  # gid -> couleur de la courbe
        self.draw([])

    def draw(self, series: Sequence[PlotSeries], title: str = "", settings: Optional[PlotSettings] = None) -> None:
        fig = self.figure
        fig.clear()
        _auto_layout(fig)
        self._traces = {}
        self.colors = {}
        self.axes = {}
        self.legend = None
        self.main_quantity = None  # grandeur de l'axe principal quand il n'y a pas de voie électrique
        ax = fig.add_subplot(111)
        self._main_ax = ax
        settings = settings or PlotSettings()

        if not series:
            ax.set_axis_off()  # l'application affiche son propre écran d'accueil
            fig.canvas.draw_idle()
            return

        from .theme import C

        # Sans voie électrique, la température (sinon l'humidité) passe sur l'axe principal
        # au lieu d'un axe secondaire à droite d'un axe gauche vide (issue #14).
        quantities = {s.quantity for s in series if not s.raw}
        climatic_only = bool(quantities) and quantities <= {TEMPERATURE, HUMIDITY}
        self.main_quantity = (TEMPERATURE if TEMPERATURE in quantities else HUMIDITY) if climatic_only else None
        axes = {MAIN_AXIS: ax}
        for quantity in (TEMPERATURE, HUMIDITY):
            if quantity in quantities and quantity != self.main_quantity:
                axes[quantity] = ax.twinx()
        if TEMPERATURE in axes and HUMIDITY in axes:
            axes[HUMIDITY].spines["right"].set_position(("axes", 1.09))

        handles = []
        main = [s for s in series if not s.raw]
        palette = _default_colors(main)
        for s in sorted(series, key=lambda s: not s.raw):  # le brut d'abord : dessous
            target = axes.get(s.quantity, ax)  # voie de l'axe principal (électrique, ou °C seule)
            if s.raw:
                (line,) = target.plot([], [], color=C["border_strong"], linewidth=0.9, label=_("_brut"), gid=s.gid,
                                      zorder=1)
                own = settings.curves.get(s.key)
                trace = _Trace(line, s.x, s.y, s.shiftable, s.index_base, absolute=bool(own and own.absolute))
                self._traces[s.gid] = trace
                self._refresh(trace, None)
                continue
            style = settings.curves.get(s.key) or CurveStyle(style=_default_dash(max(0, s.color_index)))
            color = style.color or palette[s.gid]
            label = style.name or s.label
            if style.absolute and not style.name:
                label = "|{}|".format(label)
            (line,) = target.plot([], [], color=color, linewidth=style.width, linestyle=style.style,
                                  marker=style.marker or None, markersize=3, label=label,
                                  gid=s.gid, zorder=2)
            self.colors[s.gid] = color
            trace = _Trace(line, s.x, s.y, s.shiftable, s.index_base, absolute=style.absolute)
            self._traces[s.gid] = trace
            self._refresh(trace, None)
            handles.append(line)
        for a in axes.values():
            a.relim()
            a.autoscale_view()

        if any(s.quantity not in (TEMPERATURE, HUMIDITY) for s in main):
            ax.set_ylabel(axis_title([s for s in main if s.quantity not in (TEMPERATURE, HUMIDITY)]))
        elif self.main_quantity is None:
            ax.set_yticks([])
        from .theme import HUMIDITY_COLOR, TEMPERATURE_COLOR

        # Axe de la température (de l'humidité) de la couleur de sa première courbe, même
        # si l'utilisateur l'a changée : courbe, légende et axe toujours assortis (issue #4).
        first = {q: next((self.colors[s.gid] for s in main if s.quantity == q), None) for q in (TEMPERATURE, HUMIDITY)}
        if TEMPERATURE in axes:
            color = first[TEMPERATURE] or TEMPERATURE_COLOR
            axes[TEMPERATURE].set_ylabel(_("Température (°C)"), color=color)
            axes[TEMPERATURE].tick_params(axis="y", colors=color)
        if HUMIDITY in axes:
            color = first[HUMIDITY] or HUMIDITY_COLOR
            axes[HUMIDITY].set_ylabel(_("Humidité (%HR)"), color=color)
            axes[HUMIDITY].tick_params(axis="y", colors=color)
        # Style épuré : fond blanc, bordures fines, grille discrète, textes atténués
        for a in axes.values():
            a.set_facecolor(C["card"])
            for side in ("top", "left", "bottom", "right"):
                a.spines[side].set_color(C["border_strong"])
                a.spines[side].set_linewidth(0.8)
            a.spines["top"].set_visible(False)
            a.tick_params(length=3, width=0.8, labelsize=9, color=C["border_strong"])
        ax.spines["right"].set_visible(False)
        ax.tick_params(axis="both", labelcolor=C["muted_fg"])
        ax.yaxis.label.set_color(C["fg_soft"])
        ax.xaxis.label.set_color(C["fg_soft"])
        if self.main_quantity is not None:  # axe principal = température (ou humidité), à sa couleur
            from .theme import HUMIDITY_COLOR as _HC, TEMPERATURE_COLOR as _TC

            temperature = self.main_quantity == TEMPERATURE
            color = first[self.main_quantity] or (_TC if temperature else _HC)
            ax.set_ylabel(_("Température (°C)") if temperature else _("Humidité (%HR)"), color=color)
            ax.tick_params(axis="y", labelcolor=color)
        for twin in list(axes.values())[1:]:
            twin.spines["left"].set_visible(False)
            twin.spines["bottom"].set_visible(False)

        if self.percent_axis:
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _pos=None: _("{:g} %").format(round(v, 1))))
            ax.xaxis.set_major_locator(MaxNLocator(nbins=10, steps=[1, 2, 2.5, 5, 10]))
            ax.set_xlabel(_("Avancement (% de la durée de chaque fichier)"))
        else:
            ax.xaxis.set_major_formatter(FuncFormatter(format_axis_time))
            ax.xaxis.set_major_locator(TimeLocator())
            ax.set_xlabel(_("Temps (H:MM:SS)"))
        ax.grid(True, color=C["muted"], linewidth=1.0)
        ax.set_axisbelow(True)
        ax.format_coord = self._format_coord
        title = settings.title or title
        if title:
            ax.set_title(title, fontsize=10, loc="left", color="0.35")
        self.axes = axes
        self._apply_axis_settings(settings)

        self._place_legend(ax, handles, settings.legend)
        if settings.x.min is not None or settings.x.max is not None:
            self._on_xlim_changed(ax)  # points de la période choisie
        # Zoom / déplacement : on recalcule les points visibles
        for a in axes.values():
            a.callbacks.connect("xlim_changed", self._on_xlim_changed)
        fig.canvas.draw_idle()

    def _apply_axis_settings(self, settings: PlotSettings) -> None:
        """Limites, libellés et échelles choisis par l'utilisateur (sinon : automatiques)."""
        ax = self._main_ax
        if settings.x.label is not None:
            ax.set_xlabel(settings.x.label)
        x_step = settings.x_percent_step if self.percent_axis else settings.x.step
        if x_step:
            ax.xaxis.set_major_locator(StepLocator(x_step, label_chars=6 if self.percent_axis else 8,
                                                   time=not self.percent_axis))
            ax.xaxis.set_minor_locator(StepLocator(x_step, minor=True))
        if settings.x.min is not None or settings.x.max is not None:
            ax.set_xlim(left=settings.x.min, right=settings.x.max)
        for name, a in self.axes.items():
            axis = settings.y.get(name)
            if axis is None:
                continue
            if axis.log:
                a.set_yscale("log")
            elif axis.step:
                a.yaxis.set_major_locator(StepLocator(axis.step))
                a.yaxis.set_minor_locator(StepLocator(axis.step, minor=True))
            if axis.label is not None:
                a.set_ylabel(axis.label)
            if axis.min is not None or axis.max is not None:
                a.set_ylim(bottom=axis.min, top=axis.max)

    def axis_titles(self) -> Dict[str, str]:
        """Libellé actuel de chaque axe Y affiché (pour la fenêtre de réglage)."""
        return {name: a.get_ylabel() for name, a in self.axes.items()}

    def set_time_offset(self, minutes: float) -> None:
        """Décale les courbes climatiques sans retracer le graphique."""
        self.time_offset_min = float(minutes)
        for trace in self._traces.values():
            if trace.shiftable:
                self._refresh(trace, self._view())
        self.figure.canvas.draw_idle()

    def current_view(self):
        """Période affichée (zoom) en minutes, ou None si rien n'est tracé."""
        return tuple(self._main_ax.get_xlim()) if self._traces and self._main_ax is not None else None

    def original_index(self, gid: str, plotted_index: int) -> int:
        """Indice dans les données complètes d'un point tracé."""
        trace = self._traces[gid]
        return int(trace.index_base + trace.index[plotted_index])

    def update_data(self, gid: str, y) -> None:
        """Nouvelles valeurs d'une courbe (après correction), sans retracer le reste."""
        trace = self._traces[gid]
        y = np.asarray(y, dtype=float)
        trace.y = y[trace.index_base:trace.index_base + len(trace.x)]
        self._refresh(trace, self._view())
        self.figure.canvas.draw_idle()

    # ------------------------------------------------------------------ interne

    def _view(self):
        return self._main_ax.get_xlim() if self._main_ax is not None else None

    def _on_xlim_changed(self, _ax) -> None:
        view = self._view()
        for trace in self._traces.values():
            self._refresh(trace, view)

    def _refresh(self, trace: _Trace, view) -> None:
        offset = self.time_offset_min if trace.shiftable else 0.0
        n = len(trace.x)
        if view is None:
            start, stop = 0, n
        else:
            start = max(0, int(np.searchsorted(trace.x, view[0] - offset)) - 1)
            stop = min(n, int(np.searchsorted(trace.x, view[1] - offset)) + 1)
        y = np.abs(trace.y) if trace.absolute else trace.y
        index = decimate_indices(y, start, max(start, stop))
        xs, ys = trace.x[index] + offset, y[index]
        gaps = trace.gaps[(trace.gaps >= start) & (trace.gaps < max(start, stop) - 1)]
        if len(gaps):
            # Interruption de l'enregistrement : les deux bords sont tracés, puis une coupure (NaN)
            index = np.unique(np.concatenate([index, gaps, gaps + 1]))
            cut = np.searchsorted(index, gaps, side="right")
            xs = np.insert(trace.x[index] + offset, cut, np.nan)
            ys = np.insert(y[index], cut, np.nan)
            index = np.insert(index, cut, -1)
        trace.index = index
        trace.line.set_data(xs, ys)

    def _format_coord(self, x, y):
        t = _("{:.2f} %").format(x) if self.percent_axis else format_hms(x)
        return _("t = {}   y = {:.6g}").format(t, y)

    def _place_legend(self, ax, handles, position: str) -> None:
        """Légende hors de la zone des courbes (issue #6) : à droite (plusieurs colonnes s'il y a
        beaucoup de voies), en haut ou en bas ; la mise en page réduit le tracé d'autant."""
        self.legend = None
        if not handles or position == "hidden":
            return
        fig = self.figure
        labels = [h.get_label() for h in handles]
        size = "small" if len(handles) <= 12 else "x-small"
        points = 8.3 if size == "small" else 6.9
        if position in ("top", "bottom"):
            longest = max(len(label) for label in labels)
            ncol = max(1, min(len(handles), int(fig.get_figwidth() * 72 / (points * 0.62 * longest + 34))))
        else:
            position = "right"
            rows = max(5, int((fig.get_figheight() * 72 - 24) / (points * 1.75)))
            ncol = math.ceil(len(handles) / rows)
        try:
            leg = fig.legend(handles, labels, loc=_LEGEND_LOC[position], ncol=ncol, fontsize=size,
                             framealpha=0.95, borderaxespad=self.legend_pad)
        except ValueError:  # pragma: no cover - Matplotlib < 3.7 : pas de position « outside »
            leg = ax.legend(handles, labels, loc="upper left", bbox_to_anchor=(1.12, 1.0), ncol=ncol,
                            fontsize=size, borderaxespad=0.0)
        from .theme import C

        frame = leg.get_frame()
        frame.set_edgecolor(C["border"])
        frame.set_linewidth(0.8)
        frame.set_boxstyle("round,pad=0.4,rounding_size=0.6")
        for text in leg.get_texts():
            text.set_color(C["fg_soft"])
        self.legend = leg
