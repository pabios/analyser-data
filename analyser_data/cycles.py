"""Ajout / suppression de cycles entiers (issue #12 : « un essai plus long »).

Principe :

* **Cycles** : détectés sur une voie de référence (ex. le courant d'un essai de cyclage) par
  un déclencheur à hystérésis : un cycle commence quand le signal remonte au-dessus du
  niveau haut (65 % de l'amplitude) après être passé sous le niveau bas (35 %). Un essai
  charge / repos / décharge / repos donne un seul début de cycle par période ; un signal
  alternatif est coupé à son passage montant par le milieu (« zero-crossing »).
* **Coupes et insertions au début exact d'un cycle** : la fin du cycle N-1 est suivie du
  début d'un cycle, exactement comme dans l'enregistrement d'origine. La forme d'onde est
  conservée (pas de saut ajouté, pas de fréquence parasite).
* **Raccord de niveau** : si le signal dérive (bruit de fond, offset), l'écart de niveau
  au raccord est compensé puis s'estompe sur quelques points (fondu enchaîné léger), sans
  toucher au front du cycle.
* **Toutes les voies du fichier** sont coupées / insérées aux mêmes points, et le temps
  reste continu (les cycles suivants sont avancés ou reculés).
* **Vérification** : spectre (FFT) avant / après, même fréquence fondamentale f0.

Les points ajoutés ne sont pas des mesures : ils sont repérés (masque ``synthetic``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class Cycle:
    number: int  # 1, 2, 3…
    start: int  # indice du 1er point
    stop: int  # indice après le dernier point


def detect_cycles(y, min_dwell: int = 2, spike_width: int = 5) -> List[Cycle]:
    """Cycles complets du signal (entre deux débuts de cycle consécutifs).

    La détection se fait sur une copie filtrée (médiane glissante) : les pics parasites
    étroits (``spike_width`` points) ne créent pas de faux débuts de cycle ; les fronts sont
    conservés. Une voie sans variation nette (amplitude < 8 × le bruit) n'a pas de cycles.
    """
    from scipy.ndimage import median_filter

    from .cleaning import _noise_sigma

    y = np.asarray(y, dtype=float)
    finite = np.isfinite(y)
    if finite.sum() < 10:
        return []
    if not finite.all():
        idx = np.arange(len(y))
        y = np.interp(idx, idx[finite], y[finite])
    noise = _noise_sigma(y)  # bruit mesuré sur le signal d'origine
    y = median_filter(y, size=2 * spike_width + 1, mode="nearest")
    low_p, high_p = np.percentile(y, [5, 95])
    span = high_p - low_p
    if span <= 0 or span < 8 * noise:
        return []
    low, high = low_p + 0.35 * span, low_p + 0.65 * span
    # Enregistrement qui démarre directement sur un cycle (ex. charge dès t = 0) : 1er début
    starts = [0] if y[0] > high and (y[:min_dwell + 1] > high).all() else []
    armed = bool(y[0] < low)
    i, n = 1, len(y)
    while i < n:
        if y[i] < low:
            armed = True
        elif armed and y[i] > high and y[i - 1] <= high and (y[i:i + min_dwell] > high).all():
            # Début du cycle : franchissement du niveau haut, à la même phase pour tous les
            # cycles (front d'un créneau, ou passage montant d'un signal alternatif)
            starts.append(i)
            armed = False
        i += 1
    return [Cycle(k + 1, a, b) for k, (a, b) in enumerate(zip(starts[:-1], starts[1:]))]


@dataclass
class Edit:
    """Une modification : retirer les cycles [first, last], ou dupliquer ``source`` ``count``
    fois, insérées après le cycle ``after`` (0 : avant le 1er cycle)."""
    kind: str  # "remove" ou "duplicate"
    first: int = 0
    last: int = 0
    source: int = 0
    count: int = 1
    after: int = 0


def plan(cycles: Sequence[Cycle], edit: Edit, n_points: int) -> List[Tuple[int, int]]:
    """Morceaux du signal d'origine à mettre bout à bout : liste de (début, fin) d'indices."""
    by_number = {c.number: c for c in cycles}
    if edit.kind == "remove":
        if not (edit.first in by_number and edit.last in by_number and edit.first <= edit.last):
            raise ValueError("cycles")
        a, b = by_number[edit.first].start, by_number[edit.last].stop
        return [(0, a), (b, n_points)]
    if edit.kind == "duplicate":
        if edit.source not in by_number or edit.count < 1:
            raise ValueError("cycle")
        if edit.after == 0:
            at = cycles[0].start
        elif edit.after in by_number:
            at = by_number[edit.after].stop
        else:
            raise ValueError("position")
        src = by_number[edit.source]
        return [(0, at)] + [(src.start, src.stop)] * edit.count + [(at, n_points)]
    raise ValueError(edit.kind)


def assemble(y, pieces: Sequence[Tuple[int, int]], blend: int = 8) -> np.ndarray:
    """Met bout à bout les morceaux, avec raccord de niveau progressif à chaque jonction."""
    from .cleaning import _noise_sigma

    y = np.asarray(y, dtype=float)
    finite = y[np.isfinite(y)]
    noise = _noise_sigma(finite) if len(finite) > 2 else 0.0
    out = []
    for k, (a, b) in enumerate(pieces):
        part = y[a:b].copy()
        if k and out and len(part):
            prev_end = pieces[k - 1][1]
            w = max(1, min(blend, len(part) // 10, prev_end, a))
            # Écart entre le niveau juste avant la jonction et celui qui précédait ce morceau
            # dans l'enregistrement d'origine : dérive / offset à compenser (pas le front).
            before_here = np.nanmean(y[prev_end - w:prev_end]) if prev_end >= w else np.nan
            before_origin = np.nanmean(y[a - w:a]) if a >= w else np.nan
            delta = before_here - before_origin
            # Seulement une vraie dérive (> 3 × le bruit) : un écart dû au bruit n'est pas corrigé
            if np.isfinite(delta) and abs(delta) > 3 * noise:
                part[:w] += delta * np.linspace(1.0, 0.0, w, endpoint=False)
        out.append(part)
    return np.concatenate(out) if out else y[:0].copy()


def assemble_time(t, pieces: Sequence[Tuple[int, int]], step: float) -> np.ndarray:
    """Temps continu : chaque morceau reprend un pas d'échantillonnage après le précédent."""
    t = np.asarray(t, dtype=float)
    out = []
    for a, b in pieces:
        part = t[a:b]
        if not len(part):
            continue
        if out:
            part = part - part[0] + out[-1][-1] + step
        out.append(part)
    return np.concatenate(out) if out else t[:0].copy()


def synthetic_mask(pieces: Sequence[Tuple[int, int]], edit: Edit) -> np.ndarray:
    """Points ajoutés (cycles dupliqués : pas des mesures)."""
    flags = []
    for k, (a, b) in enumerate(pieces):
        inserted = edit.kind == "duplicate" and 0 < k < len(pieces) - 1
        flags.append(np.full(b - a, inserted))
    return np.concatenate(flags) if flags else np.zeros(0, dtype=bool)


def spectrum(y, step_s: float) -> Tuple[np.ndarray, np.ndarray]:
    """Spectre d'amplitude (FFT) : (fréquences en Hz, amplitudes normalisées)."""
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)]
    if len(y) < 4 or step_s <= 0:
        return np.zeros(0), np.zeros(0)
    y = y - y.mean()
    amplitude = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    freq = np.fft.rfftfreq(len(y), step_s)
    peak = amplitude[1:].max() if len(amplitude) > 1 else 0
    return freq, amplitude / peak if peak else amplitude


def fundamental(cycles: Sequence[Cycle], step_s: float) -> Optional[float]:
    """Fréquence fondamentale f0 (Hz) : inverse de la durée médiane des cycles."""
    if not cycles or step_s <= 0:
        return None
    return 1.0 / (float(np.median([c.stop - c.start for c in cycles])) * step_s)


def spectral_f0(y, step_s: float) -> Optional[float]:
    """f0 mesurée sur le spectre (pic principal, interpolation parabolique)."""
    freq, amp = spectrum(y, step_s)
    if len(amp) < 4:
        return None
    k = int(np.argmax(amp[1:])) + 1
    if 1 <= k < len(amp) - 1:
        a, b, c = amp[k - 1], amp[k], amp[k + 1]
        denom = a - 2 * b + c
        shift = 0.5 * (a - c) / denom if denom else 0.0
        return float(freq[k] + shift * (freq[1] - freq[0]))
    return float(freq[k])
