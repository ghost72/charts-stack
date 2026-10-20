"""
Tests for the charts-download manifest (multi-source labels + backward compat).

record() now accepts a LIST of source labels and writes them as a "sources"
JSON array, while ALSO keeping the single "source" string (primary label) so
older readers still work. Old entries that only have "source" must still read
back correctly.

All tests run WITHOUT network/Docker.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config, manifest


def _manifest_at(tmp_path, monkeypatch):
    path = tmp_path / ".charts-manifest.json"
    monkeypatch.setattr(config, "MANIFEST_FILE", str(path))
    return path


def test_record_writes_sources_list_and_primary_source(tmp_path, monkeypatch):
    path = _manifest_at(tmp_path, monkeypatch)
    manifest.record("Golden.mp3", "HUNTR/X", "Golden",
                    ["charts", "hitparade"])
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data["entries"]["golden.mp3"]
    assert entry["sources"] == ["charts", "hitparade"]
    assert entry["source"] == "charts"  # primary, backward-compat
    assert entry["artist"] == "HUNTR/X"
    assert entry["album"] == "Golden"


def test_record_accepts_bare_string(tmp_path, monkeypatch):
    path = _manifest_at(tmp_path, monkeypatch)
    manifest.record("x.mp3", "A", "T", "charts")
    entry = json.loads(path.read_text(encoding="utf-8"))["entries"]["x.mp3"]
    assert entry["sources"] == ["charts"]
    assert entry["source"] == "charts"


def test_record_dedups_sources_keeping_order(tmp_path, monkeypatch):
    path = _manifest_at(tmp_path, monkeypatch)
    manifest.record("x.mp3", "A", "T", ["charts", "hitparade", "charts"])
    entry = json.loads(path.read_text(encoding="utf-8"))["entries"]["x.mp3"]
    assert entry["sources"] == ["charts", "hitparade"]


def test_record_without_sources_omits_both_keys(tmp_path, monkeypatch):
    path = _manifest_at(tmp_path, monkeypatch)
    manifest.record("x.mp3", "A", "T")
    entry = json.loads(path.read_text(encoding="utf-8"))["entries"]["x.mp3"]
    assert "sources" not in entry
    assert "source" not in entry


def test_old_single_source_entry_still_reads(tmp_path, monkeypatch):
    """An entry written by an OLD charts-download (only 'source') is valid JSON
    and round-trips through _load unchanged (readers fall back to 'source')."""
    path = _manifest_at(tmp_path, monkeypatch)
    old = {"entries": {"x.mp3": {"artist": "A", "title": "T", "album": "T",
                                 "source": "charts"}}}
    path.write_text(json.dumps(old), encoding="utf-8")
    # Record a new, unrelated song: the old entry must survive untouched.
    manifest.record("y.mp3", "B", "U", ["hitparade"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["entries"]["x.mp3"] == {"artist": "A", "title": "T",
                                        "album": "T", "source": "charts"}
    assert data["entries"]["y.mp3"]["sources"] == ["hitparade"]
