from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
from contextlib import suppress
from pathlib import Path

import pytest

TOR_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "tor.sh"

IPTABLES_BLOCK_START = 'echo "[*] Setting up iptables rules..."'
IPTABLES_BLOCK_END = 'echo "[✓] Transparent Tor routing enabled."'


def _generated_tor_healthcheck_script() -> str:
    script = TOR_SCRIPT_PATH.read_text()
    start = script.index("cat <<'HC' > /app/tor_healthcheck.sh")
    content_start = script.index("\n", start) + 1
    content_end = script.index("\nHC", content_start)
    return script[content_start:content_end]


def _tor_script_rule_lines() -> list[str]:
    """Return the iptables invocations in tor.sh, in file order.

    Rules that are applied on a best-effort basis are written as
    ``if iptables ...; then``, so the shell wrapper is stripped to keep the
    ordering assertions below comparing rules rather than syntax.
    """
    lines = []
    for raw_line in TOR_SCRIPT_PATH.read_text().splitlines():
        line = raw_line.strip().removeprefix("if ")
        if line.startswith("iptables "):
            lines.append(line)
    return lines


def _line_index(lines: list[str], needle: str) -> int:
    return next(index for index, line in enumerate(lines) if needle in line)


def _tor_iptables_block() -> str:
    """Extract the firewall setup section of tor.sh so it can be executed."""
    script = TOR_SCRIPT_PATH.read_text()
    start = script.index(IPTABLES_BLOCK_START)
    end = script.index(IPTABLES_BLOCK_END)
    return script[start:end]


# Verbatim stderr of an iptables build whose kernel lacks the xt_owner module,
# as reported from Synology DSM in issue #1150.
MISSING_XT_OWNER_STDERR = (
    "Warning: Extension owner revision 0 not supported, missing kernel module?\n"
    "iptables: No chain/target/match by that name.\n"
)

IPTABLES_STUB = f"""#!/bin/bash
if [ "$FAKE_OWNER_MATCH_SUPPORTED" != "1" ]; then
    case "$*" in
        *--uid-owner*)
            printf '%s' {MISSING_XT_OWNER_STDERR!r} >&2
            exit 1
            ;;
    esac
fi
echo "$*" >> "$FAKE_IPTABLES_LOG"
exit 0
"""

# tor.sh resolves the Tor uid with `id -u "$TOR_USER"`; that account only exists
# inside the image, so stub it out with the uid the Debian tor package uses.
ID_STUB = """#!/bin/bash
echo 107
"""


def _run_tor_iptables_block(tmp_path: Path, *, owner_match_supported: bool):
    """Run tor.sh's firewall block against a fake iptables.

    Returns the completed process plus the rules the fake iptables accepted.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in (("iptables", IPTABLES_STUB), ("id", ID_STUB)):
        stub = bin_dir / name
        stub.write_text(source)
        stub.chmod(0o755)

    applied_rules_log = tmp_path / "applied-rules.log"
    # tor.sh runs under `set -e`, which is what turns a rejected rule into a
    # container restart loop, so reproduce that here.
    result = subprocess.run(
        ["bash", "-c", "set -e\n" + _tor_iptables_block()],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "TOR_USER": "debian-tor",
            "FAKE_IPTABLES_LOG": str(applied_rules_log),
            "FAKE_OWNER_MATCH_SUPPORTED": "1" if owner_match_supported else "0",
        },
    )
    applied_rules = applied_rules_log.read_text().splitlines() if applied_rules_log.exists() else []
    return result, applied_rules


requires_bash = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is required to execute tor.sh"
)

# The watchdog shells out to `timeout`, which is not present on stock macOS.
requires_timeout = pytest.mark.skipif(
    shutil.which("timeout") is None, reason="coreutils timeout is required"
)

SUPERVISORCTL_STUB = """#!/bin/bash
# Emulates `supervisorctl status tor`. Normally reports FAKE_TOR_STATE, but when
# FAKE_TOR_FAIL_FIRST is set the first call reports a transient failure so the
# watchdog's retry behaviour can be exercised.
if [ -n "$FAKE_TOR_FAIL_FIRST" ] && [ ! -f "$FAKE_TOR_CALL_MARKER" ]; then
    touch "$FAKE_TOR_CALL_MARKER"
    echo "tor    FATAL     Exited too quickly"
    exit 3
fi
echo "tor    $FAKE_TOR_STATE   pid 42, uptime 0:01:00"
"""


def _watchdog_path_env(bin_dir: Path) -> str:
    """PATH exposing the stubs plus the real bash/timeout the watchdog calls."""
    parts = [str(bin_dir)]
    for tool in ("bash", "timeout"):
        resolved = shutil.which(tool)
        if resolved:
            parts.append(str(Path(resolved).parent))
    parts += ["/usr/bin", "/bin"]
    return ":".join(parts)


def _run_tor_watchdog(
    tmp_path: Path,
    *,
    tor_state: str = "RUNNING",
    bootstrapped: bool = True,
    trans_port_open: bool = True,
    fail_first: bool = False,
    run_for: float = 2.0,
):
    """Run tor.sh's watchdog against a fake Tor and see if it resets the container.

    Returns ``(reset_signal, stdout)`` where ``reset_signal`` is the signal the
    stand-in for PID 1 received, or ``None`` if it was left alone.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    supervisorctl = bin_dir / "supervisorctl"
    supervisorctl.write_text(SUPERVISORCTL_STUB)
    supervisorctl.chmod(0o755)

    notices_log = tmp_path / "notices.log"
    notices_log.write_text(
        "Bootstrapped 100%: Done\n" if bootstrapped else "Bootstrapped 45%: Loading\n"
    )

    script = tmp_path / "tor_healthcheck.sh"
    script.write_text(_generated_tor_healthcheck_script())
    script.chmod(0o755)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    trans_port = listener.getsockname()[1]
    if trans_port_open:
        listener.listen(5)
    else:
        # Closing it leaves a port nothing is listening on, which is what a dead
        # or wedged Tor looks like from the outside.
        listener.close()

    # Stands in for PID 1: the watchdog signals it instead of dumb-init.
    container_process = subprocess.Popen(
        ["sleep", "120"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    env = {
        "PATH": _watchdog_path_env(bin_dir),
        "TOR_CHECK_INTERVAL": "0.2",
        "TOR_NOTICES_LOG": str(notices_log),
        "TOR_TRANS_PORT": str(trans_port),
        "TOR_CONTAINER_PID": str(container_process.pid),
        "FAKE_TOR_STATE": tor_state,
        "FAKE_TOR_CALL_MARKER": str(tmp_path / "supervisorctl.called"),
    }
    if fail_first:
        env["FAKE_TOR_FAIL_FIRST"] = "1"

    watchdog = subprocess.Popen(
        ["bash", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        try:
            container_process.wait(timeout=run_for)
        except subprocess.TimeoutExpired:
            pass
        reset_signal = (
            -container_process.returncode if container_process.poll() is not None else None
        )
    finally:
        # Only the watchdog gets its own session; signalling the group is how we
        # reap the `sleep` and `timeout` children it spawns.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(watchdog.pid), signal.SIGKILL)
        with suppress(ProcessLookupError):
            container_process.kill()
        with suppress(OSError):
            listener.close()
        container_process.wait()
        stdout = watchdog.communicate()[0] or ""

    return reset_signal, stdout


BOOTSTRAP_BLOCK_START = 'if [ -f "$TOR_NOTICES_LOG" ]; then'
BOOTSTRAP_BLOCK_END = 'echo "[✓] Tor is ready."'

SUPERVISORD_STUB = """#!/bin/bash
# Emulates supervisord launching Tor, which appends this boot's progress to the
# notices log.
echo "supervisord" >> "$FAKE_CALLS_LOG"
printf '%s\\n' "$FAKE_TOR_BOOT_LINE" >> "$TOR_NOTICES_LOG"
"""

# The Tor account only exists inside the image, so record the call instead.
CHOWN_STUB = """#!/bin/bash
echo "chown $*" >> "$FAKE_CALLS_LOG"
"""


def _tor_bootstrap_block(timeout_seconds: int) -> str:
    """Extract tor.sh from the notices-log reset through the bootstrap wait."""
    script = TOR_SCRIPT_PATH.read_text()
    block = script[script.index(BOOTSTRAP_BLOCK_START) : script.index(BOOTSTRAP_BLOCK_END)]
    for original, replacement in (
        ("/usr/bin/supervisord", "supervisord"),
        ("BOOTSTRAP_TIMEOUT=300", f"BOOTSTRAP_TIMEOUT={timeout_seconds}"),
    ):
        assert original in block
        block = block.replace(original, replacement)
    return block


def _run_tor_bootstrap_wait(tmp_path: Path, *, previous_log: str | None, boot_line: str):
    """Run tor.sh's bootstrap wait against a fake Tor that logs ``boot_line``.

    ``previous_log`` is what the last boot of the same container left in the
    notices log, or ``None`` for a container's first boot. Returns the completed
    process, the notices log, and the calls made to supervisord and chown in
    order.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in (("supervisord", SUPERVISORD_STUB), ("chown", CHOWN_STUB)):
        stub = bin_dir / name
        stub.write_text(source)
        stub.chmod(0o755)

    notices_log = tmp_path / "notices.log"
    if previous_log is not None:
        notices_log.write_text(previous_log)
    calls_log = tmp_path / "calls.log"

    result = subprocess.run(
        ["bash", "-c", "set -e\n" + _tor_bootstrap_block(timeout_seconds=1)],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "TOR_NOTICES_LOG": str(notices_log),
            "TOR_USER": "debian-tor",
            "FAKE_TOR_BOOT_LINE": boot_line,
            "FAKE_CALLS_LOG": str(calls_log),
        },
        timeout=30,
    )
    calls = calls_log.read_text().splitlines() if calls_log.exists() else []
    return result, notices_log, calls


VERIFY_BLOCK_START = 'echo "[*] Verifying Tor connectivity..."'
VERIFY_BLOCK_END = "# Set correct timezone"

CURL_STUB = """#!/bin/bash
# Emulates curl against check.torproject.org: the first FAKE_CURL_FAILURES calls
# fail the way curl does when Tor cannot resolve the host yet, later calls print
# FAKE_CURL_BODY.
calls=$(cat "$FAKE_CURL_CALLS" 2>/dev/null || echo 0)
echo $((calls + 1)) > "$FAKE_CURL_CALLS"
if [ "$calls" -lt "$FAKE_CURL_FAILURES" ]; then
    echo "curl: (6) Could not resolve host: check.torproject.org" >&2
    exit 6
fi
printf '%s' "$FAKE_CURL_BODY"
"""

# The retry delay is real wall-clock time, so skip it.
SLEEP_STUB = """#!/bin/bash
exit 0
"""


def _grep_supports_pcre() -> bool:
    grep = shutil.which("grep")
    if grep is None:
        return False
    result = subprocess.run([grep, "-qP", "x"], input="x", capture_output=True, text=True)
    return result.returncode == 0


# tor.sh parses the check.torproject.org reply with `grep -P`, which the BSD grep
# on stock macOS does not have.
requires_grep_pcre = pytest.mark.skipif(
    not _grep_supports_pcre(), reason="grep with -P (PCRE) support is required"
)


def _run_tor_connectivity_check(tmp_path: Path, *, curl_failures: int, curl_body: str = ""):
    """Run tor.sh's connectivity check against a fake curl.

    Returns the completed process plus how many times curl was called.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in (("curl", CURL_STUB), ("sleep", SLEEP_STUB)):
        stub = bin_dir / name
        stub.write_text(source)
        stub.chmod(0o755)

    notices_log = tmp_path / "notices.log"
    notices_log.write_text("[warn] Failed to resolve check.torproject.org: no exit available\n")
    curl_calls = tmp_path / "curl-calls"

    grep = shutil.which("grep")
    grep_dir = f"{Path(grep).parent}:" if grep else ""
    script = TOR_SCRIPT_PATH.read_text()
    block = script[script.index(VERIFY_BLOCK_START) : script.index(VERIFY_BLOCK_END)]
    result = subprocess.run(
        ["bash", "-c", "set -e\n" + block],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:{grep_dir}/usr/bin:/bin",
            "TOR_NOTICES_LOG": str(notices_log),
            "FAKE_CURL_CALLS": str(curl_calls),
            "FAKE_CURL_FAILURES": str(curl_failures),
            "FAKE_CURL_BODY": curl_body,
        },
        timeout=30,
    )
    calls = int(curl_calls.read_text()) if curl_calls.exists() else 0
    return result, calls


def test_tor_nat_rules_bypass_private_networks_before_tcp_redirect():
    lines = _tor_script_rule_lines()
    tcp_redirect_index = _line_index(lines, "--syn -j REDIRECT --to-ports 9040")

    for cidr in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
        rule_index = _line_index(lines, f"-d {cidr} -j RETURN")
        assert rule_index < tcp_redirect_index


def test_tor_nat_rules_exempt_tor_process_before_dns_and_tcp_redirects():
    lines = _tor_script_rule_lines()

    owner_index = _line_index(lines, "-m owner --uid-owner")
    udp_dns_index = _line_index(lines, "-p udp --dport 53")
    tcp_dns_index = _line_index(lines, "-p tcp --dport 53")
    tcp_redirect_index = _line_index(lines, "--syn -j REDIRECT --to-ports 9040")

    assert owner_index < udp_dns_index
    assert owner_index < tcp_dns_index
    assert owner_index < tcp_redirect_index


def test_tor_nat_rules_handle_dns_before_tcp_redirect():
    lines = _tor_script_rule_lines()

    tcp_redirect_index = _line_index(lines, "--syn -j REDIRECT --to-ports 9040")

    assert _line_index(lines, "-p udp --dport 53") < tcp_redirect_index
    assert _line_index(lines, "-p tcp --dport 53") < tcp_redirect_index


def test_tor_healthcheck_uses_local_tor_state_without_clear_net_probe():
    healthcheck_script = _generated_tor_healthcheck_script()

    assert "google.com" not in healthcheck_script
    assert "curl " not in healthcheck_script
    assert "supervisorctl status tor" in healthcheck_script
    assert "Bootstrapped 100%" in healthcheck_script


@requires_bash
def test_tor_iptables_setup_survives_kernel_without_owner_match(tmp_path):
    """Regression test for issue #1150.

    Synology (and other NAS/embedded) kernels ship without xt_owner. The owner
    exemption is a best-effort optimisation, so a kernel that rejects it must
    not abort tor.sh and put the container into a restart loop.
    """
    result, _ = _run_tor_iptables_block(tmp_path, owner_match_supported=False)

    assert result.returncode == 0, (
        "tor.sh aborted on a kernel without xt_owner:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@requires_bash
def test_tor_routing_still_applied_when_owner_match_unsupported(tmp_path):
    """Degrading past the owner rule must not skip the rules that torify traffic."""
    _, applied_rules = _run_tor_iptables_block(tmp_path, owner_match_supported=False)

    assert any("--syn -j REDIRECT --to-ports 9040" in rule for rule in applied_rules)
    for cidr in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
        assert any(f"-d {cidr} -j RETURN" in rule for rule in applied_rules)
    for protocol in ("udp", "tcp"):
        assert any(f"-p {protocol} --dport 53" in rule for rule in applied_rules)


@requires_bash
def test_tor_warns_when_owner_match_unsupported(tmp_path):
    """The degraded path has to be visible in the logs, not silent."""
    result, _ = _run_tor_iptables_block(tmp_path, owner_match_supported=False)

    assert "owner" in result.stdout.lower()
    assert "[!]" in result.stdout


@requires_bash
def test_tor_exempts_own_traffic_when_owner_match_supported(tmp_path):
    """On a normal kernel the exemption must still be installed."""
    result, applied_rules = _run_tor_iptables_block(tmp_path, owner_match_supported=True)

    assert result.returncode == 0, result.stderr
    owner_rules = [rule for rule in applied_rules if "--uid-owner" in rule]
    assert len(owner_rules) == 1
    assert "-j RETURN" in owner_rules[0]

    owner_index = applied_rules.index(owner_rules[0])
    redirect_index = next(
        index for index, rule in enumerate(applied_rules) if "--syn -j REDIRECT" in rule
    )
    assert owner_index < redirect_index


@requires_bash
@requires_timeout
def test_tor_watchdog_resets_container_when_tor_dies(tmp_path):
    """A Tor that is no longer running must take the container down with it."""
    reset_signal, stdout = _run_tor_watchdog(tmp_path, tor_state="STOPPED")

    assert reset_signal == signal.SIGTERM, f"container was not reset:\n{stdout}"


@requires_bash
@requires_timeout
def test_tor_watchdog_resets_container_when_tor_stops_accepting_connections(tmp_path):
    """Tor can be 'running' yet unusable; the container must still be reset."""
    reset_signal, stdout = _run_tor_watchdog(tmp_path, tor_state="RUNNING", trans_port_open=False)

    assert reset_signal == signal.SIGTERM, f"container was not reset:\n{stdout}"


@requires_bash
@requires_timeout
def test_tor_watchdog_leaves_healthy_tor_alone(tmp_path):
    """A healthy Tor must never be reset, no matter how many cycles run."""
    reset_signal, stdout = _run_tor_watchdog(tmp_path)

    assert reset_signal is None, f"healthy Tor was reset:\n{stdout}"


@requires_bash
@requires_timeout
def test_tor_watchdog_retries_once_before_resetting(tmp_path):
    """A single transient failure is retried, not escalated to a reset."""
    reset_signal, stdout = _run_tor_watchdog(tmp_path, fail_first=True)

    assert reset_signal is None, f"transient failure caused a reset:\n{stdout}"
    assert "Healthcheck failed (1/2)" in stdout
    assert "Tor recovered." in stdout


@requires_bash
@requires_timeout
def test_tor_watchdog_waits_for_first_bootstrap_before_policing(tmp_path):
    """A slow first bootstrap must not be mistaken for a failure.

    Otherwise every user on a slow link would be reset into a restart loop
    before Tor ever had a chance to come up.
    """
    reset_signal, stdout = _run_tor_watchdog(
        tmp_path, tor_state="STOPPED", bootstrapped=False, trans_port_open=False
    )

    assert reset_signal is None, f"reset during initial bootstrap:\n{stdout}"
    assert "Waiting for initial Tor bootstrap" in stdout


def test_tor_watchdog_escalates_to_container_reset_not_in_place_restart():
    """The watchdog must signal PID 1 rather than bounce Tor under supervisor."""
    healthcheck_script = _generated_tor_healthcheck_script()

    assert "kill -TERM" in healthcheck_script
    assert "supervisorctl restart" not in healthcheck_script


PREVIOUS_BOOT_LOG = (
    "Oct 20 16:40:02.000 [notice] Bootstrapped 100% (done): Done\n"
    "Oct 20 17:01:08.000 [notice] Catching signal TERM, exiting cleanly.\n"
)


@requires_bash
def test_tor_bootstrap_wait_ignores_previous_boot_log(tmp_path):
    """Regression test for issue #1502.

    The container filesystem survives a restart, and Tor appends to its notices
    log. The previous boot's "Bootstrapped 100%" line must not pass for this
    boot's, or the NAT rules go live before Tor has connected to anything and
    the container fails its connectivity check on every boot from then on.
    """
    result, _, _ = _run_tor_bootstrap_wait(
        tmp_path,
        previous_log=PREVIOUS_BOOT_LOG,
        boot_line="Oct 20 17:01:30.000 [notice] Bootstrapped 5% (conn): Connecting to a relay",
    )

    assert "Tor bootstrap complete" not in result.stdout
    assert "Tor bootstrap timed out" in result.stdout
    assert result.returncode == 1


@requires_bash
def test_tor_keeps_previous_boot_log(tmp_path):
    """Starting the new log must not throw away the previous boot's history."""
    boot_line = "Oct 20 17:01:31.000 [notice] Bootstrapped 100% (done): Done"
    result, notices_log, _ = _run_tor_bootstrap_wait(
        tmp_path, previous_log=PREVIOUS_BOOT_LOG, boot_line=boot_line
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "notices.log.prev").read_text() == PREVIOUS_BOOT_LOG
    assert notices_log.read_text() == boot_line + "\n"


@requires_bash
def test_tor_hands_its_log_to_the_tor_account_before_starting_it(tmp_path):
    """Tor opens its log after dropping root and refuses to start if it can't."""
    result, notices_log, calls = _run_tor_bootstrap_wait(
        tmp_path,
        previous_log=PREVIOUS_BOOT_LOG,
        boot_line="Oct 20 17:01:31.000 [notice] Bootstrapped 100% (done): Done",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [f"chown debian-tor {notices_log}", "supervisord"]


def test_tor_drops_to_the_account_the_owner_exemption_matches():
    """As root, Tor's own relay traffic was redirected back into its TransPort.

    The NAT rules only exempt the Tor account's traffic, so Tor has to drop to
    that same account, with a data directory that account owns.
    """
    script = TOR_SCRIPT_PATH.read_text()
    torrc = script[script.index("cat <<EOF > /etc/tor/torrc") : script.index("\nEOF\n")]

    assert "TOR_USER=debian-tor\n" in script
    assert "User ${TOR_USER}\n" in torrc
    assert "DataDirectory /var/lib/tor\n" in torrc
    assert 'TOR_UID=$(id -u "$TOR_USER")' in _tor_iptables_block()


@requires_bash
def test_tor_bootstrap_wait_completes_on_first_boot(tmp_path):
    """A container's first boot has no previous log to set aside."""
    result, _, _ = _run_tor_bootstrap_wait(
        tmp_path,
        previous_log=None,
        boot_line="Oct 20 16:40:02.000 [notice] Bootstrapped 100% (done): Done",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Tor bootstrap complete" in result.stdout
    assert not (tmp_path / "notices.log.prev").exists()


@requires_bash
def test_tor_connectivity_check_failure_is_reported(tmp_path):
    """Issue #1502: a failed check stopped the container without a word.

    Under `set -e` curl's exit status (6, could not resolve host) ended tor.sh
    before anything was logged. The failure, its cause and Tor's own view of it
    have to reach the log.
    """
    result, curl_calls = _run_tor_connectivity_check(tmp_path, curl_failures=99)

    assert result.returncode == 1
    assert curl_calls == 3
    assert "curl exit 6" in result.stdout
    assert "Could not resolve host: check.torproject.org" in result.stdout
    assert "Could not reach check.torproject.org through Tor" in result.stdout
    assert "Failed to resolve check.torproject.org: no exit available" in result.stdout


@requires_bash
@requires_grep_pcre
def test_tor_connectivity_check_retries_transient_failure(tmp_path):
    """A fresh circuit failing its first lookup must not restart the container."""
    result, curl_calls = _run_tor_connectivity_check(
        tmp_path, curl_failures=1, curl_body='{"IsTor":true,"IP":"185.220.100.240"}'
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert curl_calls == 2
    assert "Success! Traffic is routed through Tor. Current IP: 185.220.100.240" in result.stdout


@requires_bash
@requires_grep_pcre
def test_tor_connectivity_check_reports_unexpected_reply(tmp_path):
    """A reply that is not the expected JSON must still say why tor.sh stopped."""
    result, _ = _run_tor_connectivity_check(
        tmp_path, curl_failures=0, curl_body="<html>Too Many Requests</html>"
    )

    assert result.returncode == 1
    assert "Traffic is NOT using Tor" in result.stdout


@requires_bash
@requires_grep_pcre
def test_tor_connectivity_check_still_fails_closed_on_clear_net(tmp_path):
    """Traffic leaving outside Tor must still stop the container."""
    result, _ = _run_tor_connectivity_check(
        tmp_path, curl_failures=0, curl_body='{"IsTor":false,"IP":"203.0.113.7"}'
    )

    assert result.returncode == 1
    assert "Traffic is NOT using Tor. Current IP: 203.0.113.7" in result.stdout
