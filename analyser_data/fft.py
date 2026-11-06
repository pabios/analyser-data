"""Spectre d'amplitude (FFT) d'une voie (issue #16).

Calcul classique d'un spectre d'amplitude « crête » à une face :

* le signal est pris sur une grille de temps régulière (pas médian de l'enregistrement) ; les
  points manquants (valeur vide, interruption d'enregistrement) sont comblés par interpolation
  linéaire et **comptés** pour que l'utilisateur le sache ;
* la valeur moyenne est retirée (elle est donnée à part) : sinon la raie à 0 Hz écrase tout ;
* fenêtre de Hann pour limiter les fuites spectrales, amplitude corrigée de la fenêtre :
  une sinusoïde d'amplitude A donne un pic de hauteur ≈ A, dans l'unité de la voie.

Rien n'est modifié dans les mesures : c'est un calcul d'affichage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .i18n import _

MIN_POINTS = 8


@dataclass
class Spectrum:
    freq: np.ndarray  # Hz
    amplitude: np.ndarray  # amplitude crête, unité de la voie (0 à 0 Hz : moyenne retirée)
    mean: float  # valeur moyenne du signal (composante continue)
    step_s: float  # pas de la grille régulière
    points: int  # points analysés
    filled: int  # points comblés par interpolation (trous, interruptions)
    peak_hz: Optional[float] = None  # fréquence du pic principal
    peak_amplitude: Optional[float] = None

    @property
    def resolution_hz(self) -> float:
        return 1.0 / (self.points * self.step_s) if self.points and self.step_s else float("nan")

    @property
    def peak_period_s(self) -> Optional[float]:
        return 1.0 / self.peak_hz if self.peak_hz else None


def amplitude_spectrum(t_min, y) -> Spectrum:
    """Spectre d'amplitude du signal ``y`` mesuré aux instants ``t_min`` (minutes).

    ValueError (message lisible) s'il y a trop peu de points mesurés."""
    t = np.asarray(t_min, dtype=float) * 60.0
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(t) & np.isfinite(y)
    if ok.sum() < MIN_POINTS:
        raise ValueError(_("Trop peu de points mesurés pour calculer un spectre (au moins {}).").format(MIN_POINTS))
    t_ok, y_ok = t[ok], y[ok]
    order = np.argsort(t_ok, kind="stable")
    t_ok, y_ok = t_ok[order], y_ok[order]
    steps = np.diff(t_ok)
    positive = steps[steps > 0]
    if not len(positive):
        raise ValueError(_("Temps constant : impossible de calculer un spectre."))
    step = float(np.median(positive))

    regular = ok.all() and np.allclose(steps, step, rtol=1e-3, atol=1e-9)
    if regular:
        values, filled = y_ok, 0
    else:
        n = int(np.floor((t_ok[-1] - t_ok[0]) / step + 1e-6)) + 1
        grid = t_ok[0] + np.arange(n) * step
        values = np.interp(grid, t_ok, y_ok)
        # Points de la grille sans mesure à moins d'un demi-pas : comblés
        nearest = np.clip(np.searchsorted(t_ok, grid), 1, len(t_ok) - 1)
        distance = np.minimum(np.abs(grid - t_ok[nearest - 1]), np.abs(t_ok[nearest] - grid))
        filled = int(np.sum(distance > 0.5 * step + 1e-9))
    n = len(values)
    if n < MIN_POINTS:
        raise ValueError(_("Trop peu de points mesurés pour calculer un spectre (au moins {}).").format(MIN_POINTS))

    mean = float(np.mean(values))
    window = np.hanning(n)
    amplitude = 2.0 * np.abs(np.fft.rfft((values - mean) * window)) / window.sum()
    amplitude[0] = 0.0
    freq = np.fft.rfftfreq(n, step)
    peak_hz = peak_amplitude = None
    if len(amplitude) > 2 and amplitude[1:].max() > 0:
        k = int(np.argmax(amplitude[1:])) + 1
        peak_hz, peak_amplitude = float(freq[k]), float(amplitude[k])
        if 1 <= k < len(amplitude) - 1:  # interpolation parabolique : fréquence plus précise
            a, b, c = amplitude[k - 1], amplitude[k], amplitude[k + 1]
            denom = a - 2 * b + c
            if denom:
                shift = 0.5 * (a - c) / denom
                peak_hz = float(freq[k] + shift * (freq[1] - freq[0]))
    return Spectrum(freq=freq, amplitude=amplitude, mean=mean, step_s=step, points=n, filled=filled,
                    peak_hz=peak_hz, peak_amplitude=peak_amplitude)


def format_frequency(hz: Optional[float]) -> str:
    """0,0123 Hz -> « 12,3 mHz » ; 1500 Hz -> « 1,5 kHz »."""
    if hz is None or not np.isfinite(hz):
        return "—"
    for factor, unit in ((1e3, "kHz"), (1.0, "Hz"), (1e-3, "mHz"), (1e-6, "µHz")):
        if abs(hz) >= factor or unit == "µHz":
            return _number("{:.4g}".format(hz / factor)) + " " + unit
    return "—"


def format_period(seconds: Optional[float]) -> str:
    """Période d'un pic : « 0,25 s », « 12,5 min », « 3:20:00 »."""
    if seconds is None or not np.isfinite(seconds):
        return "—"
    if seconds < 60:
        return _number("{:.4g}".format(seconds)) + " s"
    from .plotting import format_hms

    return format_hms(seconds / 60.0)


def _number(text: str) -> str:
    from .i18n import get_language

    return text.replace(".", ",") if get_language() == "fr" else text
