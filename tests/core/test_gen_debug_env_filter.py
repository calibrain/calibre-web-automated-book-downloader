"""genDebug.sh must keep secret-looking variables out of environment.txt.

People attach the debug bundle to public issues, so the filter is tested by running the
real pipeline line from the script against a controlled environment.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "genDebug.sh"


def _filtered_env_lines(env: dict[str, str]) -> list[str]:
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
    return out.splitlines()


def _filtered_env(env: dict[str, str]) -> set[str]:
    return {entry.split("=", 1)[0] for entry in _filtered_env_lines(env)}


def _redacted_config(tmp_path: Path, settings: dict[str, object]) -> dict[str, object]:
    match = re.search(r"^redact_config_json\(\) \{\n.*?^\}\n", SCRIPT.read_text(), re.M | re.S)
    assert match is not None
    config_file = tmp_path / "settings.json"
    config_file.write_text(json.dumps(settings, indent=2))
    out = subprocess.run(
        ["bash", "-c", f'{match.group(0)}redact_config_json "$1"', "bash", str(config_file)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


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
            "PROWLARR_MAM_ID": "h",
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
            "PROWLARR_MAM_ID",
        }
    )


def test_ordinary_variables_are_kept() -> None:
    names = _filtered_env(
        {"PATH": "/usr/bin:/bin", "TZ": "UTC", "PUID": "1000", "FLASK_PORT": "8084"}
    )

    assert {"TZ", "PUID", "FLASK_PORT"} <= names


def test_proxy_credentials_are_redacted() -> None:
    lines = _filtered_env_lines(
        {
            "PATH": "/usr/bin:/bin",
            "HTTP_PROXY": "http://alice:s3cret@proxy.local:3128",
            "HTTPS_PROXY": "socks5://bob@10.0.0.1:1080",
            "NO_PROXY": "localhost",
        }
    )

    assert "HTTP_PROXY=http://[REDACTED]@proxy.local:3128" in lines
    assert "HTTPS_PROXY=socks5://[REDACTED]@10.0.0.1:1080" in lines
    assert "NO_PROXY=localhost" in lines


def test_config_secrets_are_redacted(tmp_path: Path) -> None:
    redacted = _redacted_config(
        tmp_path,
        {
            "PROWLARR_API_KEY": "key",
            "PROWLARR_MAM_ID": "mam-session",
            "QBITTORRENT_PASSWORD": "password",
            "HTTP_PROXY": "http://alice:s3cret@proxy.local:3128",
            "PROWLARR_URL": "http://prowlarr:9696",
            "ADMIN_NOTIFICATION_ROUTES": [
                {"event": "all", "url": "tgram://123456:bot-token/987"},
                {"event": "download_failed", "url": "discord://webhook-id/webhook-token"},
            ],
            "BOOK_LANGUAGE": ["en"],
        },
    )

    assert redacted == {
        "PROWLARR_API_KEY": "[REDACTED]",
        "PROWLARR_MAM_ID": "[REDACTED]",
        "QBITTORRENT_PASSWORD": "[REDACTED]",
        "HTTP_PROXY": "http://[REDACTED]@proxy.local:3128",
        "PROWLARR_URL": "http://prowlarr:9696",
        "ADMIN_NOTIFICATION_ROUTES": [
            {"event": "all", "url": "tgram://[REDACTED]"},
            {"event": "download_failed", "url": "discord://[REDACTED]"},
        ],
        "BOOK_LANGUAGE": ["en"],
    }


def test_package_listing_does_not_need_pip() -> None:
    """The image ships without pip, so packages are listed through importlib.metadata."""
    match = re.search(r"<<'PY'.*?\n(.*?)^PY$", SCRIPT.read_text(), re.M | re.S)
    assert match is not None
    out = subprocess.run(
        [sys.executable, "-I", "-c", match.group(1)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert any(line.startswith("pytest==") for line in out.splitlines())
