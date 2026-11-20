import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("enabled,country,code,body,expected", [
    ("0", "ch", "200", "{}", "skip"),
    ("true", "ch", "200", '{"feed":{"results":[{"artistName":"A","name":"B"}]}}', "ok"),
    ("1", "de", "503", "{}", "problem"),
    ("1", "ch", "200", "{}", "problem"),
    ("1", "ch", "200", '{"feed":{"results":[]}}', "problem"),
    ("1", "../ch", "200", "{}", "problem"),
])
def test_apple_probe_is_separate_and_optional(enabled, country, code, body, expected):
    script = """
WATCHDOG_LIB_ONLY=1 . ./watch.sh
APPLE_CHARTS_ENABLED="$1"
APPLE_CHARTS_COUNTRY="$2"
TEST_CODE="$3"
TEST_BODY="$4"
http_probe() {
    printf '%s\\n' "$1" >&2
    HTTP_CODE="$TEST_CODE"
    HTTP_BODY="$TEST_BODY"
}
ext_reachable apple
ext_label apple
"""
    proc = subprocess.run(
        ["/bin/sh", "-c", script, "sh", enabled, country, code, body],
        cwd=ROOT, capture_output=True, text=True, check=True,
        env={**os.environ, "CHARTS_LANG": "en"})
    assert proc.stdout.splitlines() == [expected, "Apple Music"]
    if enabled == "0" or country == "../ch":
        assert not proc.stderr
    else:
        assert proc.stderr.strip() == (
            f"https://rss.marketingtools.apple.com/api/v2/{country}/music/most-played/100/songs.json")


def test_apple_uses_existing_debounce_and_recovery_machine(tmp_path):
    source = (ROOT / "watch.sh").read_text().replace("/tmp/wd", str(tmp_path))
    source = source.replace("ext_reachable() {", "real_ext_reachable() {")
    script = "WATCHDOG_LIB_ONLY=1\n" + source + """
CONTAINERS=""
WATCH_STATUS_ENABLED=0
APPLE_CHARTS_ENABLED=1
APPLE_CHARTS_COUNTRY=ch
PUSHOVER_TOKEN=""
PUSHOVER_USER=""
INTERVAL=1
EXT_DEBOUNCE_SECONDS=2
RECOVER_POLLS=2
POLL=0
log() { :; }
t() { printf '%s:%s\\n' "$1" "${2:-}"; }
notify() { printf '%s\\n' "$1"; }
ext_reachable() {
    if [ "$1" = apple ]; then real_ext_reachable apple; else echo skip; fi
}
http_probe() {
    if [ "$POLL" -lt 2 ]; then HTTP_CODE=503; else HTTP_CODE=200; fi
    HTTP_BODY='{"feed":{"results":[{"artistName":"A","name":"B"}]}}'
}
sleep() {
    POLL=$((POLL + 1))
    [ "$POLL" -lt 5 ] || exit 0
}
main_loop
"""
    proc = subprocess.run(
        ["/bin/sh", "-c", script], cwd=ROOT, capture_output=True, text=True,
        check=True, timeout=10)
    assert proc.stdout.splitlines() == [
        "watchdog.notify.ext_problem_title:Apple Music",
        "watchdog.notify.ext_recovered_title:Apple Music",
    ]
    assert (tmp_path / "ext-apple.alarm").read_text().strip() == "0"
