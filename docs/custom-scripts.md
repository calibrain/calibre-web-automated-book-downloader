# Custom Scripts

Shelfmark can run an executable you provide after a download task completes successfully. The script runs after the selected output has finished (for example: transfer to the folder destination, upload to Grimmory, or send by email).


## Quick Start (Recommended)

1. Put your script on the machine that runs Shelfmark.
1. Make it executable.
1. Set it in Shelfmark (Settings -> Advanced -> Custom Script Path).

Example:

```bash
chmod +x /path/to/your/scripts/post_process.sh
```

### Docker Users

If you run Shelfmark in Docker, the script must exist inside the container. The easiest way is to mount a folder of scripts, then point Shelfmark at the container path in the UI.

```yaml
services:
  shelfmark:
    image: ghcr.io/calibrain/shelfmark:latest
    volumes:
      - /path/to/your/scripts:/scripts:ro
```

Then set:

- Settings -> Advanced -> Custom Script Path: `/scripts/post_process.sh`

<details>
<summary>Docker Compose: Configure Via Environment Variables (Optional)</summary>

```yaml
services:
  shelfmark:
    environment:
      - CUSTOM_SCRIPT=/scripts/post_process.sh
      - CUSTOM_SCRIPT_PATH_MODE=absolute
      - CUSTOM_SCRIPT_JSON_PAYLOAD=true
```

</details>

## Script Behaviour

When enabled, Shelfmark runs your script once per successful task:

```bash
<custom_script_path> "<target_path>"
```

- `$1` is always set to the target path.
- If **Custom Script JSON Payload** is enabled, Shelfmark writes a JSON document to stdin (UTF-8).
- If JSON payload is disabled, stdin is empty (EOF).
- Timeout: 300 seconds (5 minutes)
- Exit code: `0` = success; anything else = the task is marked as **Error**
- Concurrency: downloads can run in parallel, so your script may be invoked concurrently for different tasks.
- Runtime: the script runs inside the Shelfmark container (if you use Docker) under the same user as Shelfmark.

## The Target Path (`$1`)

Shelfmark chooses a "best single path" for the task:

- If the output produced exactly one local file: that file path.
- If the output produced multiple local files: a directory path (the common parent directory of those files).

What the target path refers to depends on the output mode:

- Folder output (`output.mode=folder`, `phase=post_transfer`): the final imported file or folder inside your destination.
- Grimmory output (`output.mode=booklore`, `phase=post_upload`): the local file or folder that was uploaded (the destination is remote).
- Email output (`output.mode=email`, `phase=post_email`): the file that was sent, or the folder holding the files when there were several.

By default, `$1` is an absolute path inside the Shelfmark container (or on your host, if you are not using Docker).

## JSON Payload (stdin)

Configure in: Settings -> Advanced -> Custom Script JSON Payload

When enabled, Shelfmark sends a versioned JSON payload to your script via stdin (and still passes `$1`). This is the recommended way to write robust scripts, especially for multi-file imports (audiobooks) and output-specific context (like Grimmory).

- The JSON payload always includes absolute paths in `paths.*`, even if you set Custom Script Path Mode to `relative` for `$1`.
- `output.mode` tells you which output ran.
- `output.details` is output-specific. For Grimmory output (the mode value is still `booklore`), `output.details.booklore` includes connection details such as `base_url`, `library_id`, and `path_id`. For email output, `output.details.email` has `to`, `label`, `host`, `port`, and `security`.
- `phase` indicates when the script is running. Current values: `post_transfer` (folder output), `post_upload` (Grimmory output), `post_email` (email output).
- `transfer` is only included for outputs that do a local transfer (for example the folder output).

If JSON payload is disabled, stdin is empty (EOF). Don't `cat` stdin unless you've enabled the payload.

Example payload shape:

```json
{
  "version": 1,
  "phase": "post_transfer",
  "task": {
    "task_id": "abc123",
    "source": "direct",
    "title": "Foundation",
    "author": "Isaac Asimov"
  },
  "output": {
    "mode": "folder",
    "organization_mode": "organize"
  },
  "paths": {
    "destination": "/data/library/books",
    "target": "/data/library/books/Isaac Asimov/Foundation/Foundation.epub",
    "final_paths": [
      "/data/library/books/Isaac Asimov/Foundation/Foundation.epub"
    ]
  },
  "transfer": {
    "op_counts": {"copy": 1, "move": 0, "hardlink": 0},
    "use_hardlink": false,
    "is_torrent": false,
    "preserve_source": false
  }
}
```

Example (bash + python3) (JSON payload must be enabled):

```bash
payload="$(cat)"
target="$1"
PAYLOAD="$payload" TARGET="$target" python3 - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["PAYLOAD"])

print(f"target={os.environ['TARGET']}", file=sys.stderr)
print(
    f"mode={payload['output']['mode']} title={payload['task']['title']}",
    file=sys.stderr,
)
for path in payload["paths"]["final_paths"]:
    print(path, file=sys.stderr)
PY
```

Example (Python) (works whether JSON payload is enabled or not):

```python
#!/usr/bin/env python3
import json
import sys

target = sys.argv[1]
raw = sys.stdin.read()
payload = json.loads(raw) if raw.strip() else None

print(f"target={target}", file=sys.stderr)
if payload:
    print(f"mode={payload['output']['mode']} phase={payload['phase']}", file=sys.stderr)
```

<details>
<summary>Advanced Options</summary>

### Absolute vs Relative Target Paths

Configure in: Settings -> Advanced -> Custom Script Path Mode

This setting controls what gets passed as `$1`:

- `absolute` (default): pass an absolute path.
- `relative`: pass a path relative to the output's "destination root", and run the script with `$PWD` set to that root.

For folder output, the destination root is your configured destination folder. For Grimmory output, it's the local upload folder. For email output, it's the folder holding the files that were sent.

Example (folder destination is `/data/library/books`, and the imported file ended up in `Isaac Asimov/Foundation/Foundation.epub`):

```bash
# Absolute mode:
$PWD is unchanged
$1 = /data/library/books/Isaac Asimov/Foundation/Foundation.epub

# Relative mode:
$PWD = /data/library/books
$1 = Isaac Asimov/Foundation/Foundation.epub
```

Note: if the target is the destination folder itself, `relative` mode may pass `.`.

</details>

## Notes And Caveats

- **Hardlinks and torrents:** if you use hardlinking to keep seeding, avoid scripts that modify file contents, since hardlinked files share data with the seeding copy.
- **Grimmory output mode:** scripts run after upload. `$1` will point at the local uploaded file (or staging folder).
- **Email output mode:** scripts run after the message is sent. `$1` will point at the file that was sent (or the folder holding the files).
