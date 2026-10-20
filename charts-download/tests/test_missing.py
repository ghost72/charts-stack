"""
Tests for charts-download missing-file merging (multi-source labels).

A chart song can appear in several missing files (e.g. missing.txt -> "charts"
and missing-hitparade.txt -> "hitparade"). load_all must keep the song ONCE but
collect EVERY source label (deduplicated, in file order), so the manifest and
the Pushover messages can list all contributing playlists.

All tests run WITHOUT network/Docker.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import missing


def _write_missing(path, lines):
    """Write a minimal missing file ('  <pos>. Artist - Title')."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Fehlende Chart-Songs\n\n")
        for pos, text in lines:
            fh.write(f"  {pos}. {text}\n")


def test_song_in_two_files_collects_both_labels_in_order(tmp_path):
    charts = tmp_path / "missing.txt"
    hitparade = tmp_path / "missing-hitparade.txt"
    # Ashe - Stop the Wedding! is in BOTH files.
    _write_missing(charts, [(1, "Ashe - Stop the Wedding!"),
                            (2, "Remi Wolf - Bottle")])
    _write_missing(hitparade, [(3, "Ashe - Stop the Wedding!")])

    result = missing.load_all([str(charts), str(hitparade)])

    # The overlapping song appears exactly ONCE.
    ashe = [e for e in result if e[1] == "Ashe"]
    assert len(ashe) == 1
    _pos, _artist, _title, sources = ashe[0]
    # Both labels, in file order (charts first = primary), deduplicated.
    assert sources == ["charts", "hitparade"]

    # A song only in the first file carries just that label.
    remi = [e for e in result if e[1] == "Remi Wolf"][0]
    assert remi[3] == ["charts"]


def test_same_song_twice_in_one_file_is_not_duplicated(tmp_path):
    charts = tmp_path / "missing.txt"
    _write_missing(charts, [(1, "Ashe - Stop the Wedding!"),
                            (5, "Ashe - Stop the Wedding!")])
    result = missing.load_all([str(charts)])
    assert len(result) == 1
    assert result[0][3] == ["charts"]


def test_position_sort_and_exclude_artists_preserved(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "EXCLUDE_ARTISTS", ["Nemo"])
    charts = tmp_path / "missing.txt"
    _write_missing(charts, [(9, "Zeynep - Inat"),
                            (2, "Nemo - The Code"),
                            (4, "Sido - Issa")])
    result = missing.load_all([str(charts)])

    # Nemo excluded, remaining sorted ascending by position.
    titles = [e[2] for e in result]
    assert titles == ["Issa", "Inat"]
    assert all(e[3] == ["charts"] for e in result)
