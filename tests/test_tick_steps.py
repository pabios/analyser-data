"""Issue #17 : intervalle entre deux graduations, choisi pour chaque axe."""
from pathlib import Path

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from analyser_data.channels import HUMIDITY, TEMPERATURE
from analyser_data.plotting import (MAIN_AXIS, PlotManager, PlotSettings, StepLocator, format_tick_step,
                                    parse_tick_step)
from analyser_data.session import Session

EXAMPLES = Path(__file__).resolve().parent.parent / "exemples"


@pytest.mark.parametrize("text, minutes", [
    ("Automatique", None), ("", None), ("30 min", 30), ("1 h", 60), ("2 h", 120), ("6 h", 360), ("12 h", 720),
    ("1 jour", 1440), ("1:30:00", 90), ("0:00:30", 0.5), ("15", 15), ("15 min", 15), ("3 h", 180), ("2 j", 2880),
    ("1,5 h", 90), ("45 s", 0.75)])
def test_time_steps(text, minutes):
    assert parse_tick_step(text, time_axis=True) == pytest.approx(minutes) if minutes else \
        parse_tick_step(text, time_axis=True) is None


@pytest.mark.parametrize("text, value", [("Automatique", None), ("5", 5), ("0,5", 0.5), ("10", 10), ("2.5", 2.5)])
def test_value_steps(text, value):
    assert parse_tick_step(text, time_axis=False) == value


@pytest.mark.parametrize("text, time_axis", [("abc", True), ("-1 h", True), ("0", False), ("x", False),
                                             ("-5", False), ("1:99:00", True)])
def test_invalid_steps_are_refused(text, time_axis):
    with pytest.raises(ValueError, match="Pas de graduation invalide"):
        parse_tick_step(text, time_axis)


def test_format_round_trip():
    for value in (None, 30.0, 60.0, 1440.0, 90.0):
        assert parse_tick_step(format_tick_step(value, True), True) == value
    for value in (None, 5.0, 0.5, 2.5):
        assert parse_tick_step(format_tick_step(value, False), False) == value


def test_locator_uses_the_step_and_stays_readable():
    ticks = StepLocator(60).tick_values(0, 74 * 60)  # 74 h, toutes les heures
    assert ticks[0] == 0 and np.allclose(np.diff(ticks), 60) and len(ticks) == 75
    ticks = StepLocator(0.1).tick_values(0.05, 0.62)
    assert list(ticks) == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]  # pas de 0,30000000000000004
    ticks = StepLocator(30).tick_values(0, 30 * 1440)  # 30 jours toutes les 30 min : multiple du pas
    assert len(ticks) <= 101 and np.allclose(ticks % 30, 0)


def test_steps_are_applied_to_every_axis():
    s = Session()
    s.load_files([str(EXAMPLES / "GL980_Mes-_260601-170139.CSV"), str(EXAMPLES / "nanodac_Rd_Z.txt")])
    gl, nano = list(s.measurements)
    keys = [(gl, "Channel 4"), (nano, "Channel 2")]
    settings = PlotSettings()
    settings.x.step = 5.0  # 5 min
    settings.y_axis(MAIN_AXIS).step = 2.0
    settings.y_axis(TEMPERATURE).step = 0.5
    fig = Figure(figsize=(14, 7))  # taille du graphique dans la fenêtre de l'appli
    FigureCanvasAgg(fig)
    plot = PlotManager(fig)
    plot.draw(s.series(keys), settings=settings)
    fig.canvas.draw()
    x = plot.axes[MAIN_AXIS].get_xticks()
    assert np.allclose(np.diff(x), 5.0) and np.allclose(x % 5.0, 0)
    minor = plot.axes[MAIN_AXIS].xaxis.get_minorticklocs()
    assert np.allclose(np.minimum(minor % 5.0, 5.0 - minor % 5.0), 0)
    assert np.allclose(np.diff(plot.axes[MAIN_AXIS].get_yticks()), 2.0)
    assert np.allclose(np.diff(plot.axes[TEMPERATURE].get_yticks()), 0.5)
    # « Maison » remet les limites automatiques mais garde les pas
    settings.clear_limits()
    assert settings.x.step == 5.0


def test_steps_are_remembered():
    settings = PlotSettings()
    settings.x.step = 60.0
    settings.y_axis(HUMIDITY).step = 10.0
    saved = settings.tick_steps()
    assert saved == {"x": 60.0, HUMIDITY: 10.0}
    again = PlotSettings()
    again.set_tick_steps(saved)
    assert again.x.step == 60.0 and again.y[HUMIDITY].step == 10.0
    again.set_tick_steps({"x": "abc", MAIN_AXIS: -1})  # configuration abîmée : ignorée
    assert again.x.step == 60.0 and MAIN_AXIS not in again.y


def test_app_reloads_saved_steps(tmp_path, monkeypatch):
    from analyser_data import app, i18n

    monkeypatch.setattr(i18n, "CONFIG_FILE", tmp_path / "config.json")
    i18n.set_setting("tick_steps", {"x": 120.0, TEMPERATURE: 5.0})
    settings = app.initial_plot_settings()
    assert settings.x.step == 120.0 and settings.y[TEMPERATURE].step == 5.0


@pytest.mark.parametrize("text, seconds", [("1 s", 1), ("5 s", 5), ("10 s", 10), ("30 s", 30), ("1 min", 60),
                                           ("2 min", 120), ("5 min", 300), ("10 min", 600), ("15 min", 900)])
def test_short_time_steps_in_list(text, seconds):
    """Demande du 2026-11-13 (issue #16) : pas plus fins que 30 min, dans la liste."""
    from analyser_data.plotting import TIME_TICK_STEPS

    assert text in TIME_TICK_STEPS.values()
    value = parse_tick_step(text, time_axis=True)
    assert value * 60 == pytest.approx(seconds)
    assert format_tick_step(value, time_axis=True) == text  # mémorisé puis réaffiché tel quel


@pytest.mark.parametrize("text", ["0,5 s", "0:00:00", "0,001"])
def test_time_step_below_one_second_refused(text):
    with pytest.raises(ValueError):
        parse_tick_step(text, time_axis=True)


def test_ten_second_ticks_on_a_short_test():
    """Essai de 9 min : graduations toutes les 10 s, étiquettes H:MM:SS distinctes."""
    from analyser_data.plotting import format_axis_time

    ticks = StepLocator(10 / 60).tick_values(0, 9.37)
    assert len(ticks) == 57 and ticks[1] * 60 == pytest.approx(10)
    labels = [format_axis_time(t) for t in ticks]
    assert labels[:3] == ["0:00:00", "0:00:10", "0:00:20"] and len(set(labels)) == len(labels)


def test_labels_never_overlap_but_the_chosen_step_stays_visible():
    """10 s sur un essai de 9 min dans un petit graphique : étiquettes à un multiple rond de
    10 s (lisibles), petites graduations toutes les 10 s."""
    from analyser_data.channels import VOLTAGE
    from analyser_data.plotting import PlotSeries

    x = np.arange(0, 9.37, 0.1 / 60)
    fig = Figure(figsize=(8, 4))
    FigureCanvasAgg(fig)
    plot = PlotManager(fig)
    settings = PlotSettings()
    settings.x.step = 10 / 60
    plot.draw([PlotSeries(gid="0", label="U", x=x, y=np.sin(x), unit="V", quantity=VOLTAGE, key=("f", "U"))],
              settings=settings)
    fig.canvas.draw()
    ax = plot.axes[MAIN_AXIS]
    major = ax.get_xticks()
    step_s = np.diff(major)[0] * 60
    assert len(major) <= 15 and step_s in (20, 30, 60, 120, 300)
    every = np.union1d(np.round(major, 9), np.round(ax.xaxis.get_minorticklocs(), 9))
    assert np.allclose(np.diff(every) * 60, 10)  # grille complète toutes les 10 s
    boxes = [t.get_window_extent() for t in ax.get_xticklabels() if t.get_text()]
    assert all(a.x1 <= b.x0 for a, b in zip(boxes, boxes[1:]))  # aucune étiquette ne se chevauche
