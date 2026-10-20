"""genDebug.sh must keep secret-looking variables out of environment.txt.

People attach the debug bundle to public issues, so the filter is tested by running the
real pipeline line from the script against a controlled environment.
"""

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "genDebug.sh"


def _filtered_env(env: dict[str, str]) -> set[str]:
    line = next(
        line for line in SCRIPT.read_text().splitlines() if line.startswith("env | grep -v")
    )
    pipeline = re.sub(r'\s*>\s*"\$LOG_DIR/environment\.txt"\s*$', "", line)
    out = subprocess.run(
        ["bash", "-c", pipeline],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {entry.split("=", 1)[0] for entry in out.splitlines()}


def test_secret_variables_are_left_out() -> None:
    names = _filtered_env(
        {
            "PATH": "/usr/bin:/bin",
            "SHELFMARK_API_KEY": "a",
            "SHELFMARK_API_KEY_READONLY": "b",
            "HARDCOVER_API_KEY": "c",
            "AA_DONATOR_KEY": "d",
            "SOME_SECRET": "e",
            "DB_PASSWORD_FILE": "f",
            "SERVICE_TOKEN_VALUE": "g",
        }
    )

    assert names.isdisjoint(
        {
            "SHELFMARK_API_KEY",
            "SHELFMARK_API_KEY_READONLY",
            "HARDCOVER_API_KEY",
            "AA_DONATOR_KEY",
            "SOME_SECRET",
            "DB_PASSWORD_FILE",
            "SERVICE_TOKEN_VALUE",
        }
    )


def test_ordinary_variables_are_kept() -> None:
    names = _filtered_env(
        {"PATH": "/usr/bin:/bin", "TZ": "UTC", "PUID": "1000", "FLASK_PORT": "8084"}
    )

    assert {"TZ", "PUID", "FLASK_PORT"} <= names
