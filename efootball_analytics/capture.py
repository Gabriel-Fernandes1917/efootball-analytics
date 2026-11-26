from pathlib import Path


class DesktopCapture:
    def save(self, destination: Path) -> None:
        from mss import MSS
        from mss.tools import to_png

        with MSS() as desktop:
            if len(desktop.monitors) < 2:
                raise RuntimeError("No desktop monitor is available.")
            screenshot = desktop.grab(desktop.primary_monitor)
            to_png(screenshot.rgb, screenshot.size, output=str(destination))
