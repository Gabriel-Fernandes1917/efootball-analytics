import pytest

from efootball_analytics.config import Config, Settings, default_shortcuts, parse_shortcut
from efootball_analytics.models import Event


def test_default_bindings_and_settings_round_trip(tmp_path):
    config = Config(tmp_path / "settings.json")
    assert list(config.load().bindings()) == [f"<f{key}>" for key in range(6, 12)]
    settings = Settings(False, default_shortcuts() | {Event.GOAL: "Shift+Ctrl+G"})
    config.save(settings)
    assert config.load() == settings
    assert settings.bindings()["<ctrl>+<shift>+g"] == Event.GOAL


def test_duplicate_combinations_are_rejected():
    settings = Settings(shortcuts=default_shortcuts() | {
        Event.GOAL: "Ctrl+Shift+G", Event.FORMATION: "shift+ctrl+g",
    })
    with pytest.raises(ValueError, match="different shortcut"):
        settings.bindings()


@pytest.mark.parametrize("value", ["a", "F13", "ctrl+ctrl+g", "ctrl+", "enter"])
def test_invalid_shortcuts(value):
    with pytest.raises(ValueError):
        parse_shortcut(value)


def test_invalid_config_loads_with_hotkeys_disabled(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{", encoding="utf-8")
    assert Config(path).load() == Settings(enabled=False)
