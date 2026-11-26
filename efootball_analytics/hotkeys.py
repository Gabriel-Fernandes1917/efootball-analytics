from collections.abc import Callable
from functools import partial
from typing import Protocol

from .config import Settings
from .models import Event


class Listener(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


class Hotkeys:
    def __init__(self, callback: Callable[[Event], None]) -> None:
        self.callback = callback
        self.listener: Listener | None = None

    def apply(self, settings: Settings) -> None:
        self.stop()
        if not settings.enabled:
            return
        from pynput.keyboard import GlobalHotKeys

        listener = GlobalHotKeys({
            key: partial(self.callback, event) for key, event in settings.bindings().items()
        })
        listener.start()
        self.listener = listener

    def stop(self) -> None:
        if self.listener is not None:
            self.listener.stop()
            self.listener = None
