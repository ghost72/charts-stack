"""
Unit tests for playlist_library.write_missing - the 4-tuple missing writer.

Regression focus: the year must NEVER leak into the file body (charts-download
format "  <pos>. <artist> - <title>"), even when the tuple carries a year.
"""

import pytest

from app import config, playlist_library


@pytest.fixture(autouse=True)
def tmp_charts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    return tmp_path


def test_body_has_no_year_character(tmp_charts):
    path = playlist_library.write_missing(
        [(1, "a-ha", "Take On Me", "1985")],
        "missing-energy-library.txt")
    text = (tmp_charts / "missing-energy-library.txt").read_text("utf-8")
    body_lines = [ln for ln in text.splitlines()
                  if ln and not ln.startswith("#")]
    assert body_lines == ["  1. a-ha - Take On Me"]
    # The year must appear nowhere in the body.
    assert "1985" not in "\n".join(body_lines)
    assert path.endswith("missing-energy-library.txt")


def test_positions_and_format(tmp_charts):
    playlist_library.write_missing(
        [(1, "Artist One", "Title One", "1990"),
         (2, "Artist Two", "Title Two", "")],
        "missing-energy-library.txt")
    text = (tmp_charts / "missing-energy-library.txt").read_text("utf-8")
    body_lines = [ln for ln in text.splitlines()
                  if ln and not ln.startswith("#")]
    assert body_lines == [
        "  1. Artist One - Title One",
        "  2. Artist Two - Title Two",
    ]
