"""Unit tests for the watch.sh `_status_field` helper (G0).

Each test shells out to /bin/sh, sources watch.sh in lib-only mode
(WATCHDOG_LIB_ONLY=1 so the main loop never starts), then calls
`_status_field <field> <fixture>` against a canned status JSON and asserts the
extracted string. cwd is the charts-watchdog dir so `./watch.sh` resolves.
"""

import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHDOG_DIR = os.path.dirname(HERE)


def _status_field(field, fixture_path):
    """Run `_status_field <field> <fixture>` via /bin/sh and return stdout."""
    script = (
        'WATCHDOG_LIB_ONLY=1 . ./watch.sh; '
        '_status_field "$1" "$2"'
    )
    proc = subprocess.run(
        ["/bin/sh", "-c", script, "sh", field, fixture_path],
        cwd=WATCHDOG_DIR,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    # The helper prints the value with a trailing newline; strip it.
    return proc.stdout.rstrip("\n")


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_status_ok(tmp_path):
    f = _write(
        tmp_path,
        "charts-sync.json",
        '{\n  "service": "charts-sync",\n  "status": "ok",\n'
        '  "message": "all good"\n}\n',
    )
    assert _status_field("status", f) == "ok"
    assert _status_field("message", f) == "all good"


def test_status_error(tmp_path):
    f = _write(
        tmp_path,
        "charts-energy.json",
        '{\n  "service": "charts-energy",\n  "status": "error",\n'
        '  "message": "source/format break: deezer"\n}\n',
    )
    assert _status_field("status", f) == "error"
    assert _status_field("message", f) == "source/format break: deezer"


def test_status_idle(tmp_path):
    f = _write(
        tmp_path,
        "charts-energy.json",
        '{\n  "service": "charts-energy",\n  "status": "idle"\n}\n',
    )
    assert _status_field("status", f) == "idle"


def test_status_running(tmp_path):
    f = _write(
        tmp_path,
        "charts-import.json",
        '{\n  "service": "charts-import",\n  "status": "running"\n}\n',
    )
    assert _status_field("status", f) == "running"


def test_missing_field(tmp_path):
    # No "status" line at all -> empty string.
    f = _write(
        tmp_path,
        "charts-digest.json",
        '{\n  "service": "charts-digest",\n  "message": "no status here"\n}\n',
    )
    assert _status_field("status", f) == ""


def test_absent_file(tmp_path):
    f = str(tmp_path / "does-not-exist.json")
    assert _status_field("status", f) == ""


def test_half_written_file(tmp_path):
    # A truncated mid-write file with no status line yet -> empty (quiet).
    f = _write(
        tmp_path,
        "charts-flashback.json",
        '{\n  "service": "charts-flashback",\n  "last_run_start": "2026-',
    )
    assert _status_field("status", f) == ""
