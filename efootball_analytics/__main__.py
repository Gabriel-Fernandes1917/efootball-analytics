import logging
import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from .capture import DesktopCapture
from .config import Config
from .database import Database
from .logging_setup import configure_logging
from .sessions import Sessions
from .ui import Window


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("eFootball Analytics")
    app.setStyle("Fusion")
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "eFootball Analytics"
    try:
        root.mkdir(parents=True, exist_ok=True)
        configure_logging(root / "app.log")
        sessions = Sessions(Database(root / "matches.sqlite3"), root, DesktopCapture())
        window = Window(sessions, Config(root / "settings.json"))
    except Exception as exc:
        logging.exception("Application startup failed")
        QMessageBox.critical(None, "eFootball Analytics", f"Could not start the application.\n{exc}")
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
