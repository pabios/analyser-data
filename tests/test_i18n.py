"""Langues : chaque texte de l'interface a sa traduction anglaise."""
import ast
import re
from pathlib import Path

import pytest

from analyser_data import i18n
from analyser_data.i18n_en import EN

SOURCES = sorted((Path(__file__).resolve().parent.parent / "analyser_data").glob("*.py"))
DISPLAY_LISTS = ("TIME_MODES", "EXPORT_STEPS", "PEAK_MODES", "APP_TITLE", "FILE_TYPES", "TOOLTIPS", "LINE_STYLES", "MARKERS", "AXIS_NAMES", "SCALES", "QUANTITY_NAMES", "LEGEND_POSITIONS")


def translatable_strings():
    found = set()
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_" and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                found.add(node.args[0].value)
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id in DISPLAY_LISTS:
                for sub in ast.walk(stmt.value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and " " in sub.value:
                        found.add(sub.value)
    return found


def needs_translation(text):
    # texte français (lettres hors des champs {…}) ; les formats purs et noms propres n'en ont pas besoin
    words = re.sub(r"\{[^}]*\}", "", text)
    if text.startswith("*") or re.match(r"^[\d,.]+ \w+$", text):
        return False  # motifs de fichiers, durées (« 100 ms »)
    return bool(re.search(r"[A-Za-zÀ-ÿ]{2,}", words)) and words.strip() not in {
        "Analyser DATA", "min", "MIN … MAX", "SUGGESTION", "POINTS", "Export", "Image", "Import",
        "points", "_brut", "raw:", "t =   y =", "ms", "s", "j", "1 min"}


@pytest.mark.parametrize("text", sorted(t for t in translatable_strings() if needs_translation(t)))
def test_every_text_has_an_english_translation(text):
    assert text in EN, "traduction anglaise manquante dans i18n_en.py"


def test_switch_language(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "CONFIG_FILE", tmp_path / "config.json")
    i18n.set_language("en")
    try:
        assert i18n._("Ouvrir des fichiers…") == "Open files…"
        assert i18n.load_language() == "en"  # choix mémorisé
    finally:
        i18n.set_language("fr")
    assert i18n._("Ouvrir des fichiers…") == "Ouvrir des fichiers…"


def test_language_kept_from_old_cleantrace_config(tmp_path, monkeypatch):
    """Renommage (issue #5) : la langue choisie dans l'ancienne version est reprise."""
    monkeypatch.setattr(i18n, "CONFIG_FILE", tmp_path / ".analyser_data.json")
    monkeypatch.setattr(i18n, "LEGACY_CONFIG_FILE", tmp_path / ".cleantrace.json")
    (tmp_path / ".cleantrace.json").write_text('{"language": "en"}', encoding="utf-8")
    try:
        assert i18n.load_language() == "en"
        i18n.set_language("fr")
        assert '"fr"' in (tmp_path / ".analyser_data.json").read_text(encoding="utf-8")
        assert i18n.load_language() == "fr"  # le nouveau fichier prime
    finally:
        i18n.set_language("fr", save=False)


def test_old_name_is_gone_from_the_interface():
    """Issue #5 : « Analyser DATA » partout ; « CleanTrace » seulement dans les notes d'historique."""
    from analyser_data import APP_NAME

    assert APP_NAME == "Analyser DATA"
    for path in SOURCES:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "CleanTrace" in line:
                assert "Anciennement" in line or "ancien" in line.lower(), (path.name, line)
