"""POSIX parse check for watch.sh (G0).

`sh -n` parses the script without executing it; a clean exit 0 confirms the
shell syntax (including the inserted _status_field helper and third loop) is
valid POSIX /bin/sh.
"""

import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHDOG_DIR = os.path.dirname(HERE)


def test_watch_sh_parses():
    proc = subprocess.run(
        ["/bin/sh", "-n", "watch.sh"],
        cwd=WATCHDOG_DIR,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
