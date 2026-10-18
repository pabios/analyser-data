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
    return np.unique(np.concatenate(([start], idx, [stop - 1])))


class _Trace:
    """Une courbe affichée : données complètes + indices actuellement tracés."""

    def __init__(self, line, x, y, shiftable, index_base=0, absolute=False):
        self.index_base = index_base
        self.absolute = absolute
        self.line = line
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        self.shiftable = shiftable
        self.index = np.arange(0)


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

    def draw(self, series: Sequence[PlotSeries], title: str = "", settings: Optional[PlotSettings] = None,
             spans: Sequence[Tuple[float, float]] = ()) -> None:
        """``spans`` : périodes NON MESURÉES (cycles ajoutés, issue #12), hachurées."""
        fig = self.figure
        fig.clear()
        _auto_layout(fig)
        self._traces = {}
        self.colors = {}
        self.axes = {}
        self.legend = None
        ax = fig.add_subplot(111)
        self._main_ax = ax
        settings = settings or PlotSettings()

        if not series:
            ax.set_axis_off()  # l'application affiche son propre écran d'accueil
            fig.canvas.draw_idle()
            return

        from .theme import C

        axes = {MAIN_AXIS: ax}
        if any(s.quantity == TEMPERATURE for s in series):
            axes[TEMPERATURE] = ax.twinx()
        if any(s.quantity == HUMIDITY for s in series):
            axes[HUMIDITY] = ax.twinx()
            if TEMPERATURE in axes:
                axes[HUMIDITY].spines["right"].set_position(("axes", 1.09))

        handles = []
        main = [s for s in series if not s.raw]
        palette = _default_colors(main)
        for s in sorted(series, key=lambda s: not s.raw):  # le brut d'abord : dessous
            target = axes.get(s.quantity, ax)
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
        else:
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

        if spans:
            from matplotlib.patches import Patch

            for x0, x1 in spans:
                ax.axvspan(x0, x1, facecolor="#f59e0b", alpha=0.12, hatch="///", edgecolor="#d97706",
                           linewidth=0, zorder=0)
            handles.append(Patch(facecolor="#f59e0b", alpha=0.25, hatch="///", edgecolor="#d97706",
                                 label=_("Cycles ajoutés (non mesurés)")))
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
        if settings.x.min is not None or settings.x.max is not None:
            ax.set_xlim(left=settings.x.min, right=settings.x.max)
        for name, a in self.axes.items():
            axis = settings.y.get(name)
            if axis is None:
                continue
            if axis.log:
                a.set_yscale("log")
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
        trace.index = decimate_indices(y, start, max(start, stop))
        trace.line.set_data(trace.x[trace.index] + offset, y[trace.index])

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
