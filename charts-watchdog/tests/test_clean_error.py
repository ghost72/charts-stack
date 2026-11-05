"""Unit tests for the watch.sh `_clean_error` helper.

`_clean_error` turns a raw service status message (which can embed an unreadable
urllib3/requests connection dump) into a short, phone-friendly line while
keeping the human cause. Each test sources watch.sh in lib-only mode
(WATCHDOG_LIB_ONLY=1 so the main loop never starts) and calls the helper.
"""

import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHDOG_DIR = os.path.dirname(HERE)


def _clean_error(message):
    """Run `_clean_error "$1"` via /bin/sh and return stdout (no trailing NL)."""
    script = "WATCHDOG_LIB_ONLY=1 . ./watch.sh; _clean_error \"$1\""
    proc = subprocess.run(
        ["/bin/sh", "-c", script, "sh", message],
        cwd=WATCHDOG_DIR,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.rstrip("\n")


def test_empty_stays_empty():
    assert _clean_error("") == ""


def test_plain_message_untouched():
    # A clean, human message has no noise to strip.
    assert _clean_error("source/format break: deezer") == \
        "source/format break: deezer"


def test_slskd_connection_pool_dump_trimmed():
    # The real-world slskd case: the human prefix survives, the
    # HTTPConnectionPool dump (host/port/retries) is dropped, no dangling
    # separator remains.
    raw = (
        "Verbindung zu slskd fehlgeschlagen: HTTPConnectionPool("
        "host='slskd', port=5030): Max retries exceeded with url: "
        "/api/v0/server (Caused by NewConnectionError('<urllib3..."
        ">: Failed to establish a new connection: [Errno 111] "
        "Connection refused'))"
    )
    assert _clean_error(raw) == "Verbindung zu slskd fehlgeschlagen"


def test_https_connection_pool_dump_trimmed():
    raw = "API down: HTTPSConnectionPool(host='x', port=443): boom"
    assert _clean_error(raw) == "API down"


def test_max_retries_without_pool_prefix_trimmed():
    raw = "deezer unreachable - Max retries exceeded with url: /chart/0"
    assert _clean_error(raw) == "deezer unreachable"


def test_very_long_message_is_capped():
    raw = "x" * 500
    out = _clean_error(raw)
    # Capped near 160 chars plus an ellipsis; must not be the full 500.
    assert len(out) < 200
    assert out.endswith("\u2026")
