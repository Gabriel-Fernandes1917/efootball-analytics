# eFootball Analytics 0.1

Windows desktop app for collecting match screenshots and recording their metadata locally. Capture uses the Windows desktop image exposed through `mss`. Global shortcuts are observed through `pynput`. The app uses ordinary desktop input and display APIs and operates independently of the game process.

## Install and launch on Windows

Install 64-bit Python 3.12 or newer. Open PowerShell in the project folder and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[test]"
.\.venv\Scripts\python.exe -m efootball_analytics
```

If you installed a newer Python version, use its version in the launcher command, such as `py -3.13`. For application-only installation, use `pip install .` inside the virtual environment.

## Capture a match

1. Enter your team and opponent and select **Start match**. The app generates a match ID and records the start date and time. An unfinished match remains active when the app reopens.
2. Bring the game to the foreground on your Windows primary display and use a global shortcut. The app captures the full primary display. You can also click an event button to capture whatever is currently visible on that display.
3. Optionally enter a minute before the next capture. For a saved capture, select its row to preview it, enter a corrected minute such as `90+3`, and select **Save minute**. Save an empty field to clear the value.
4. Capture the final result while the match is active, then select **Finish match**. The history dropdown also shows completed matches. **Delete capture** removes the selected screenshot and its database record after confirmation.

| Shortcut | Event |
| --- | --- |
| F6 | Formation |
| F7 | Substitution |
| F8 | Goal |
| F9 | Near miss |
| F10 | Match statistics |
| F11 | Final result |

Use **Global shortcuts** to edit the bindings or turn them off. When enabled, bindings apply system-wide during an active match. The corresponding keypress also reaches the foreground application. Validate screenshot visibility and shortcuts in your own eFootball setup. Exclusive fullscreen may affect capture. If the preview is blank, try borderless or windowed mode.

## Classify a substitution

Press **F7** or click **Substitution** to save a screenshot and open the reason dialog. Choose **Physical / Fatigue**, **Tactical**, **Poor Performance**, or **Other**, and add notes if needed. Cancel keeps the screenshot available for classification later. Select a substitution in **Capture history** and click **Edit substitution reason** to add or revise its reason and notes.

The database adds nullable classification fields on startup and preserves existing matches and screenshots. The reason describes the whole capture. Future per-player substitutions can be stored as child records keyed by capture ID, each with its own outgoing player, incoming player, and reason.

## Local data

The app stores data under `%LOCALAPPDATA%\eFootball Analytics`.

| Location | Contents |
| --- | --- |
| `matches.sqlite3` | Match IDs, teams, opponent names, start and finish times, and capture IDs, timestamps, categories, minutes, and relative file paths |
| `screenshots/<match-id>/<event>/<capture-id>.png` | PNG screenshots |
| `settings.json` | Shortcut settings, created when saved |
| `app.log` | Rotating application log |

Timestamps are written in UTC and displayed in local time. Saved capture IDs and relative paths provide a stable interface for a future background extraction process to read captures after their metadata commits.

## Project layout

| Module | Responsibility |
| --- | --- |
| `ui.py` | PySide6 window, preview, history, and settings dialog |
| `sessions.py` | Match lifecycle, in-game minute validation, and capture persistence workflow |
| `capture.py` | Desktop screenshot adapter |
| `hotkeys.py` | Global keyboard listener |
| `database.py` | SQLite schema and queries |
| `config.py` | Shortcut validation and local configuration |
| `models.py` | Match, capture, and event types |
| `logging_setup.py` | Rotating application logging |
| `__main__.py` | Application startup |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Tests cover match lifecycle, persistence, capture and delete failure handling, and shortcut configuration. Live game compatibility requires a Windows gaming session.
