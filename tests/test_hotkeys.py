import sys
from types import ModuleType

from efootball_analytics.config import Settings
from efootball_analytics.hotkeys import Hotkeys
from efootball_analytics.models import Event


def test_listener_delivers_events_and_is_replaced_or_stopped(monkeypatch):
    calls = []
    listeners = []

    class FakeListener:
        def __init__(self, bindings):
            self.bindings = bindings
            self.running = False
            listeners.append(self)

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

    backend = ModuleType("pynput.keyboard")
    backend.GlobalHotKeys = FakeListener
    monkeypatch.setitem(sys.modules, "pynput.keyboard", backend)
    hotkeys = Hotkeys(calls.append)
    hotkeys.apply(Settings())
    listeners[0].bindings["<f8>"]()
    assert calls == [Event.GOAL]
    hotkeys.apply(Settings())
    assert [listener.running for listener in listeners] == [False, True]
    hotkeys.apply(Settings(enabled=False))
    assert [listener.running for listener in listeners] == [False, False]
    assert hotkeys.listener is None
