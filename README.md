# eFootball Analytics 0.1

Windows desktop app for collecting match screenshots and recording their metadata locally, with optional background synchronization to SILO. Capture uses the Windows desktop image exposed through `mss`. Global shortcuts are observed through `pynput`. The app uses ordinary desktop input and display APIs and operates independently of the game process.

## Install and launch on Windows

Install 64-bit Python 3.12 or newer. Open PowerShell in the project folder and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[test]"
.\.venv\Scripts\python.exe -m efootball_analytics
```

The runtime dependencies are PySide6-Essentials, mss, pynput, and boto3. If you installed a newer Python version, use its version in the launcher command, such as `py -3.13`. For application-only installation, use `pip install .` inside the virtual environment.

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

## Synchronize to the Raw Data Lake

The app saves the original screenshot locally and commits its metadata and a durable upload entry together in SQLite. **Sync automatically** is on by default and is saved between launches. The Data Lake panel shows connection status, the pending, synchronized, and failed counts, and the last successful sync time. Pending includes uploads in progress. Hover over the status for the latest upload error.

### Set up SILO access

Use the existing private `efootball-raw` bucket. In SILO Console at <http://localhost:9001>, use the administration controls to create a dedicated user or restrict an application access key to the policy below. Console labels depend on the SILO deployment. With MinIO's Console, policies are under **IAM Policies** and credentials are under **Access Keys**. A child access key inherits its parent's permissions and can be restricted further. Save the generated secret when creating the key. [MinIO access-key documentation](https://docs.min.io/aistor/administration/console/security-and-access/)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::efootball-raw"
    },
    {
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": [
        "arn:aws:s3:::efootball-raw/screenshots/*",
        "arn:aws:s3:::efootball-raw/metadata/*",
        "arn:aws:s3:::efootball-raw/metadata-revisions/*",
        "arn:aws:s3:::efootball-raw/tombstones/*"
      ]
    }
  ]
}
```

`ListBucket` supports the signed bucket connectivity probe. The other permission covers object uploads. Use a separately authorized Console account to browse and download objects. [MinIO policy documentation](https://docs.min.io/aistor/administration/iam/access/)

Set the following variables in the PowerShell session that launches the app. The prompts keep credentials out of shell history. The app reads the process environment at launch.

```powershell
$env:S3_ENDPOINT = 'http://localhost:9000'
$env:S3_BUCKET_RAW = 'efootball-raw'
$env:S3_REGION = 'us-east-1'
$env:S3_ACCESS_KEY = Read-Host 'SILO application access key'
$siloSecret = Read-Host 'SILO application secret key' -AsSecureString
$env:S3_SECRET_KEY = [System.Net.NetworkCredential]::new('', $siloSecret).Password
Remove-Variable siloSecret
.\.venv\Scripts\python.exe -m efootball_analytics
```

HTTP is intended for local development at the loopback endpoint. For remote storage set `S3_ENDPOINT` to an HTTPS URL with a trusted certificate. If credentials are absent, captures stay queued on disk. Keep the endpoint and bucket stable for a given local database because synchronization progress is tracked against that destination.

### Verify and recover

1. Launch the app and wait for **SILO connected**. With automatic sync off, select **Sync to Data Lake** to check the connection and process pending and failed work.
2. Capture a test screenshot and wait for the synchronized count to increase. In the Console's **Object Browser**, open `efootball-raw`. Verify that the screenshot and the corresponding JSON share the same match and capture IDs. The JSON includes the SHA-256 of the original image bytes.
3. For a failed upload, hover over the Data Lake panel to see the latest error. Check Docker, the endpoint, permissions, and the Raw bucket name. Restore a missing screenshot to its original local path if needed. Restart after changing environment variables, then select **Sync to Data Lake** to retry immediately.
4. The automatic worker uses up to five attempts per revision with exponential backoff. A successful bucket probe requeues work blocked by a bucket or connection outage, including after restart. Keep the app open while syncing. Closing it leaves unfinished work on disk for the next launch.

```text
screenshots/match_id=<id>/capture_id=<id>.png
metadata/match_id=<id>/capture_id=<id>.json
metadata-revisions/match_id=<id>/capture_id=<id>/revision=<n>.json
tombstones/match_id=<id>/capture_id=<id>.json
```

On first launch with the integration, SQLite creates `capture_sync` and `capture_sync_revisions` and queues existing captures, including finished matches. Existing IDs, paths, and timestamps are used as stored. The revision journal keeps each metadata edit. The current JSON is updated after immutable revision JSON is archived, and the original screenshot is uploaded unchanged. A numeric minute is written as a JSON number. Stoppage time such as `90+3` is preserved as a string, and blank optional values are written as null.

Deleting a capture removes it locally and queues a tombstone. Any uploaded image remains in Raw. Downstream consumers should treat `tombstones/` as authoritative for deletion, including if an earlier request was already in flight. A capture deleted before upload may reference an absent image and have a null checksum. Images, immutable revisions, and tombstones use conditional writes so retries keep stable keys. The S3 endpoint must support `PutObject` with `If-None-Match: *`.

## Local data

The app stores data under `%LOCALAPPDATA%\eFootball Analytics`.

| Location | Contents |
| --- | --- |
| `matches.sqlite3` | Match IDs, teams, opponent names, start and finish times, and capture IDs, timestamps, categories, minutes, and relative file paths |
| `screenshots/<match-id>/<event>/<capture-id>.png` | PNG screenshots |
| `settings.json` | Shortcut settings, created when saved |
| `data-lake.ini` | Automatic synchronization preference |
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
| `outbox.py` | Durable upload queue and metadata revisions |
| `datalake.py` | S3 connection, background synchronization, and retry handling |
| `config.py` | Shortcut validation and local configuration |
| `models.py` | Match, capture, and event types |
| `logging_setup.py` | Rotating application logging |
| `__main__.py` | Application startup |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Tests cover match lifecycle, capture and delete failure handling, shortcut configuration, and Data Lake upload, recovery, edits, deletion, and GUI responsiveness. The Data Lake tests use temporary databases and mocked S3 operations. Live game and SILO verification use the steps above.
