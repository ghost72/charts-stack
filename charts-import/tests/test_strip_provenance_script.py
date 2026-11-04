"""
Tests for scripts/strip_provenance_tags.py: the default mode is a read-only
report; writing happens only for a validated --apply-list (inside MUSIC_DIR,
existing .mp3), removes only stamped frames from the 9-frame list, keeps real
label text and content frames, and preserves mtime. Temporary files only.
"""

import importlib.util
import os
import pathlib

import pytest

from app import config

_SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts" / "strip_provenance_tags.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("strip_prov_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mp3(path, frames):
    from mutagen import id3 as m
    header = b"\xff\xfb\x90\x40"
    with open(path, "wb") as fh:
        fh.write((header + b"\x00" * 413) * 30)
    t = m.ID3()
    t.add(m.TIT2(encoding=3, text=["Power"]))
    t.add(m.TXXX(encoding=3, desc="MusicBrainz Album Id", text=["abc"]))
    for fid, val in frames:
        t.add(m.WORS(url=val) if fid == "WORS"
              else getattr(m, fid)(encoding=3, text=[val]))
    t.save(str(path))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "PRESERVE_MTIME", False)
    return music


def _keys(path):
    from mutagen.id3 import ID3
    return set(str(k) for k in ID3(str(path)).keys())


def test_report_mode_never_writes(lib, capsys):
    s = _load_script()
    p = lib / "A" / "x.mp3"
    p.parent.mkdir()
    _mp3(p, [("TIT3", "fordjonly.com"), ("TPUB", "Universal Music")])
    before = p.read_bytes()
    assert s.main([]) == 0
    out = capsys.readouterr().out
    assert "A/x.mp3\tTIT3\tfordjonly.com" in out
    assert "TPUB" not in out
    assert p.read_bytes() == before


def test_apply_list_cleans_only_listed_and_stamped(lib, tmp_path):
    s = _load_script()
    listed = lib / "listed.mp3"
    other = lib / "curated.mp3"
    _mp3(listed, [("TIT3", "fordjonly.com"), ("TPUB", "Universal Music"),
                  ("TCOP", "fordjonly.com")])
    _mp3(other, [("TIT3", "fordjonly.com")])
    os.utime(listed, (1_000_000_000, 1_000_000_000))
    lst = tmp_path / "list.txt"
    lst.write_text("# reviewed\nlisted.mp3\tTIT3\tfordjonly.com\n\n",
                   encoding="utf-8")
    assert s.main(["--apply-list", str(lst)]) == 0
    keys = _keys(listed)
    assert "TIT3" not in keys and "TCOP" not in keys
    assert "TPUB" in keys and "TIT2" in keys
    assert any(k.startswith("TXXX:MusicBrainz") for k in keys)
    assert int(os.stat(listed).st_mtime) == 1_000_000_000
    # Not on the list -> untouched.
    assert "TIT3" in _keys(other)


def test_apply_list_rejects_outside_and_missing(lib, tmp_path, capsys):
    s = _load_script()
    outside = tmp_path / "outside.mp3"
    _mp3(outside, [("TIT3", "fordjonly.com")])
    lst = tmp_path / "list.txt"
    lst.write_text(f"{outside}\n../outside.mp3\nnope.mp3\n", encoding="utf-8")
    assert s.main(["--apply-list", str(lst)]) == 0
    assert "TIT3" in _keys(outside)
    assert capsys.readouterr().out.count("SKIP") == 3


_EXCLUDED = [".charts-trash/x", "@eaDir", "#recycle", ".stfolder"]


def test_excluded_folders_not_reported_but_normal_is(lib, capsys):
    s = _load_script()
    for d in _EXCLUDED:
        p = lib / "A" / d / "s.mp3" if d == "@eaDir" else lib / d / "s.mp3"
        p.parent.mkdir(parents=True)
        _mp3(p, [("TIT3", "fordjonly.com")])
    ok = lib / "A" / "ok.mp3"
    _mp3(ok, [("TIT3", "fordjonly.com")])
    assert s.main([]) == 0
    out = capsys.readouterr().out
    assert "A/ok.mp3\tTIT3" in out
    assert out.count("\n") == 1


def test_apply_list_refuses_excluded_folders(lib, tmp_path, capsys):
    s = _load_script()
    entries = []
    for d in _EXCLUDED:
        p = lib / d / "s.mp3"
        p.parent.mkdir(parents=True)
        _mp3(p, [("TIT3", "fordjonly.com")])
        # Absolute paths: a relative "#recycle/..." line would be read as a
        # '#' comment by the list parser.
        entries.append(str(p))
    ok = lib / "A" / "ok.mp3"
    ok.parent.mkdir()
    _mp3(ok, [("TIT3", "fordjonly.com")])
    entries.append("A/ok.mp3")
    lst = tmp_path / "list.txt"
    lst.write_text("\n".join(entries) + "\n", encoding="utf-8")
    assert s.main(["--apply-list", str(lst)]) == 0
    out = capsys.readouterr().out
    assert out.count("SKIP (inside excluded folder)") == len(_EXCLUDED)
    for d in _EXCLUDED:
        assert "TIT3" in _keys(lib / d / "s.mp3")
    assert "TIT3" not in _keys(ok)


def test_apply_list_dry_run_writes_nothing(lib, tmp_path):
    s = _load_script()
    p = lib / "x.mp3"
    _mp3(p, [("TIT3", "fordjonly.com")])
    lst = tmp_path / "list.txt"
    lst.write_text("x.mp3\n", encoding="utf-8")
    assert s.main(["--apply-list", str(lst), "--dry-run"]) == 0
    assert "TIT3" in _keys(p)
