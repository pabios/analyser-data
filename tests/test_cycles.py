"""Issue #12 : ajouter / retirer des cycles entiers (« un essai plus long »)."""
from pathlib import Path

import numpy as np
import pytest

from analyser_data.cycles import Edit, assemble, detect_cycles, plan, spectral_f0, spectrum
from analyser_data.loader import TIME_COL
from analyser_data.session import Session

EXAMPLES = Path(__file__).resolve().parent.parent / "exemples"
GL980 = str(EXAMPLES / "GL980_Mes-_260601-170139.CSV")


def square(n_cycles=5, period=600, noise=0.01, seed=0):
    """Charge 4/10, repos 1/10, décharge 4/10, repos 1/10, comme les essais du client."""
    rng = np.random.default_rng(seed)
    t = np.arange(n_cycles * period)
    phase = (t % period) / period
    y = np.where(phase < 0.4, 5.0, np.where((phase >= 0.5) & (phase < 0.9), -5.0, 0.0))
    return y + rng.normal(0, noise, len(t))


def test_cycles_detected_at_their_exact_start():
    y = square()
    cycles = detect_cycles(y)
    assert [c.start for c in cycles] == [0, 600, 1200, 1800]
    assert all(c.stop - c.start == 600 for c in cycles)


def test_spikes_and_flat_channels_make_no_cycles():
    y = square()
    y[[100, 101, 350, 1500]] = 10.0  # pics parasites
    assert len(detect_cycles(y)) == 4
    assert detect_cycles(24 + np.random.default_rng(1).normal(0, 0.02, 5000)) == []


@pytest.mark.parametrize("edit, length, n_cycles", [
    (Edit("duplicate", source=2, count=3, after=4), 3000 + 1800, 7),
    (Edit("duplicate", source=1, count=1, after=0), 3600, 5),
    (Edit("remove", first=2, last=3), 1800, 2),
])
def test_edit_keeps_the_waveform_and_f0(edit, length, n_cycles):
    y = square()
    pieces = plan(detect_cycles(y), edit, len(y))
    after = assemble(y, pieces)
    assert len(after) == length
    assert len(detect_cycles(after)) == n_cycles
    # forme conservée : chaque cycle complet est identique (au bruit près) au créneau d'origine
    ref = square(noise=0)[:600]
    for c in detect_cycles(after):
        assert np.abs(after[c.start:c.stop] - ref).max() < 0.1
    # même fréquence fondamentale, pas de fréquence parasite (pic principal inchangé)
    assert spectral_f0(after, 1.0) == pytest.approx(spectral_f0(y, 1.0), rel=0.02)
    freq, amp = spectrum(after, 1.0)
    assert freq[np.argmax(amp[1:]) + 1] == pytest.approx(1 / 600, rel=0.05)


def test_level_drift_is_blended_without_touching_the_edge():
    y = square(noise=0) + np.linspace(0, 0.5, 3000)  # dérive lente
    pieces = plan(detect_cycles(y), Edit("duplicate", source=1, count=1, after=4), len(y))
    after = assemble(y, pieces, blend=8)
    junction = pieces[0][1]
    assert abs(after[junction - 1] - y[junction - 1]) < 1e-9  # avant la jonction : intact
    assert abs(np.diff(after[junction - 3:junction])).max() < 0.01  # pas de marche ajoutée au repos
    assert after[junction] > 4.5  # le front du cycle inséré est conservé


def test_session_edits_every_channel_of_the_file_and_can_undo():
    s = Session()
    s.load_files([GL980])
    name = next(iter(s.measurements))
    m = s.measurements[name]
    before = {c: m.data[c].to_numpy().copy() for c in m.data.columns}
    key = (name, "Channel 7")
    cycles = s.detect_cycles(key)
    assert len(cycles) == 3
    preview = s.preview_cycle_edit(key, Edit("duplicate", source=2, count=2, after=3))
    assert preview["f0_after"] == pytest.approx(preview["f0_before"], rel=0.02)
    assert len(m.data) == 24000  # aperçu : rien n'est modifié

    added = s.apply_cycle_edit(key, Edit("duplicate", source=2, count=2, after=3))
    period = cycles[1].stop - cycles[1].start
    assert added == 2 * period and len(m.data) == 24000 + added
    assert all(len(m.data[c]) == len(m.data) for c in m.data.columns)  # toutes les voies
    t = m.data[TIME_COL].to_numpy()
    assert np.allclose(np.diff(t), m.period_s / 60.0, atol=1e-6)  # temps continu
    mask = s.synthetic[name]
    assert mask.sum() == added and mask[cycles[2].stop:cycles[2].stop + added].all()
    np.testing.assert_array_equal(m.data["Channel 3"].to_numpy()[cycles[2].stop:cycles[2].stop + period],
                                  before["Channel 3"][cycles[1].start:cycles[1].stop])  # autre voie copiée aux mêmes points
    spans = s.synthetic_spans()
    assert len(spans) == 1 and spans[0][1] - spans[0][0] == pytest.approx((added - 1) * m.period_s / 60.0)
    assert "AJOUTÉS, NON MESURÉS" in s.journal_text()
    assert s.modified_points(key) == 0  # le brut suit la même modification

    assert s.undo_cycle_edit(name)
    assert len(m.data) == 24000 and name not in s.synthetic
    np.testing.assert_array_equal(m.data["Channel 4"].to_numpy(), before["Channel 4"])


def test_remove_cycles_shortens_the_test():
    s = Session()
    s.load_files([GL980])
    name = next(iter(s.measurements))
    cycle = s.detect_cycles((name, "Channel 7"))[1]
    removed = s.apply_cycle_edit((name, "Channel 7"), Edit("remove", first=2, last=2))
    assert removed == -(cycle.stop - cycle.start) and abs(removed) == pytest.approx(6000, abs=2)  # 10 min
    assert len(s.measurements[name].data) == 24000 + removed
    assert name not in s.synthetic or not s.synthetic[name].any()  # rien d'ajouté


def test_added_cycles_are_flagged_in_csv_and_report_without_hatching(tmp_path):
    import pandas as pd
    from matplotlib.figure import Figure

    from analyser_data.plotting import PlotManager
    from analyser_data.report import _plot_figure, _summary_page

    s = Session()
    s.load_files([GL980])
    name = next(iter(s.measurements))
    key = (name, "Channel 7")
    added = s.apply_cycle_edit(key, Edit("duplicate", source=1, count=1, after=3))

    out = tmp_path / "export.csv"
    s.export_csv(out, [key, (name, "Channel 4")])
    df = pd.read_csv(out, sep=";", decimal=",", encoding="utf-8-sig")
    flag = df["Cycle ajouté (non mesuré)"]
    assert flag.sum() == added and set(flag.unique()) == {0, 1}

    plot = PlotManager(Figure())
    plot.draw(s.series([key]))
    assert [t.get_text() for t in plot.legend.get_texts()] == ["Channel 7 (A)"]  # pas de hachure (client)
    fig = _plot_figure(s, [key], None, "Rapport")
    assert not fig.axes[0].patches
    summary = _summary_page(s, [key], None, "Rapport")
    assert any("NON MESURÉS" in t.get_text() for t in summary.texts)
