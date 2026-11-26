import json
import pathlib
import re
import shutil
import subprocess

import pytest


def test_missing_metadata_filters_multiple_fields_and_unreadable():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to exercise the browser filter")
    html = (pathlib.Path(__file__).parent.parent / "app/static/index.html").read_text(
        encoding="utf-8"
    )
    match = re.search(r"function metadataMatchesFilter\(song, filter\) \{.*?\n\}", html, re.S)
    assert match
    songs = [
        {"missing": ["year", "track"]},
        {"missing": ["track"]},
        {"missing": ["album"]},
        {"unreadable": True},
    ]
    script = (
        match.group(0)
        + "\nconst songs = " + json.dumps(songs) + ";\n"
        + "console.log(JSON.stringify(['all', 'year', 'album', 'track', 'unreadable']"
          ".map(filter => songs.map(song => metadataMatchesFilter(song, filter)))));"
    )
    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [
        [True, True, True, True],
        [True, False, False, False],
        [False, False, True, False],
        [True, True, False, False],
        [False, False, False, True],
    ]
