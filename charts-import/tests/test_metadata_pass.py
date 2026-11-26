"""
Tests for the periodic manifest-scoped metadata pass (backfill._metadata_pass).

No network: resolve.resolve_metadata is monkeypatched. Pins the end-to-end
manifest walk (fills a missing year), the never-re-query state memory (a settled
file is skipped on the next pass with zero resolver calls), the per-pass limit,
the disabled no-op, AND the manifest scope (a library file that is NOT in the
manifest is skipped entirely while a manifest song IS processed).
"""

import json
import os

import pytest

from app import backfill, config, metadata_report, resolve
from app.resolve import ResolvedMeta


def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _tag(path, **easy):
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    for k, v in easy.items():
        audio[k] = v
    audio.save()


def _read(path, key):
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    val = audio.get(key)
    return (str(val[0]) if isinstance(val, list) else str(val)) if val else ""


def _write_manifest(path: str, entries: dict) -> None:
    """Write a manifest in the nested {"entries": {...}} shape the index reads."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"entries": entries}, fh)


def _add_song(music_dir, manifest, artist, title, *, in_manifest, **easy):
    """
    Create one library file under <artist>/<title>.mp3 (so the filename-title
    matches the manifest title key) and, when in_manifest, register it in the
    manifest so the manifest-scoped pass considers it. Returns the file path.
    """
    adir = os.path.join(music_dir, artist)
    os.makedirs(adir, exist_ok=True)
    p = os.path.join(adir, f"{title}.mp3")
    _write_dummy_mp3(p)
    _tag(p, artist=artist, title=title, **easy)
    if in_manifest:
        manifest[f"{artist} - {title}"] = {
            "artist": artist, "title": title, "source": "charts",
        }
    return p


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", True)
    monkeypatch.setattr(config, "METADATA_BACKFILL_ENABLED", True)
    monkeypatch.setattr(config, "METADATA_BACKFILL_LIMIT", 50)
    monkeypatch.setattr(config, "BACKFILL_SLEEP_MS", 0)


@pytest.fixture
def library(tmp_path, monkeypatch):
    music = tmp_path / "music"
    music.mkdir(parents=True)
    manifest_path = str(tmp_path / "manifest.json")
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "MANIFEST_FILE", manifest_path)
    entries: dict = {}
    # One manifest song with no year -> fillable.
    p = _add_song(str(music), entries, "A", "T", in_manifest=True, album="T")
    _write_manifest(manifest_path, entries)
    return str(music), p


def test_pass_fills_missing_year(library, monkeypatch):
    music, p = library
    calls = {"n": 0}

    def _resolve(a, t):
        calls["n"] += 1
        return ResolvedMeta(year="1991", album="T", track="")

    monkeypatch.setattr(resolve, "resolve_metadata", _resolve)
    res = backfill._metadata_pass(music)
    assert res["year_filled"] == 1
    assert _read(p, "date") == "1991"
    assert calls["n"] == 1


def test_settled_file_not_requeried_next_pass(library, monkeypatch):
    music, p = library
    calls = {"n": 0}

    def _resolve(a, t):
        calls["n"] += 1
        return ResolvedMeta(year="1991", album="T", track="")

    monkeypatch.setattr(resolve, "resolve_metadata", _resolve)
    backfill._metadata_pass(music)   # first pass: 1 call, fills + marks done
    assert calls["n"] == 1
    backfill._metadata_pass(music)   # second pass: file settled -> no new call
    assert calls["n"] == 1


def test_disabled_is_noop(library, monkeypatch):
    music, p = library
    monkeypatch.setattr(config, "METADATA_BACKFILL_ENABLED", False)
    monkeypatch.setattr(resolve, "resolve_metadata",
                        lambda a, t: ResolvedMeta(year="1991"))
    res = backfill._metadata_pass(music)
    assert res == {"scanned": 0, "year_filled": 0, "year_replaced": 0,
                   "album_filled": 0, "track_filled": 0, "errors": 0}
    assert _read(p, "date") == ""  # untouched


def test_limit_caps_corrections(tmp_path, monkeypatch):
    music = tmp_path / "music"
    music.mkdir(parents=True)
    manifest_path = str(tmp_path / "manifest.json")
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "MANIFEST_FILE", manifest_path)
    entries: dict = {}
    for i in range(3):
        _add_song(str(music), entries, "A", f"T{i}", in_manifest=True,
                  album=f"T{i}")
    _write_manifest(manifest_path, entries)
    monkeypatch.setattr(config, "METADATA_BACKFILL_LIMIT", 2)
    monkeypatch.setattr(resolve, "resolve_metadata",
                        lambda a, t: ResolvedMeta(year="1991"))
    res = backfill._metadata_pass(str(music))
    assert res["year_filled"] == 2  # capped at the limit


def test_non_manifest_file_skipped(tmp_path, monkeypatch):
    """
    CHANGE 1: a library file that is NOT in the manifest (a hand-curated /
    back-catalog song) must be skipped ENTIRELY by the pass, while a manifest
    song IS processed. The resolver is only ever called for the manifest song.
    """
    music = tmp_path / "music"
    music.mkdir(parents=True)
    manifest_path = str(tmp_path / "manifest.json")
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "MANIFEST_FILE", manifest_path)

    entries: dict = {}
    # A manifest song (fillable: no year).
    mani = _add_song(str(music), entries, "Chart Artist", "Chart Song",
                     in_manifest=True, album="Chart Song")
    # A curated file NOT in the manifest (also fillable, but must be untouched).
    curated = _add_song(str(music), entries, "Curated Artist", "Curated Song",
                        in_manifest=False, album="Curated Song")
    _write_manifest(manifest_path, entries)

    seen = {"artists": []}

    def _resolve(a, t):
        seen["artists"].append(a)
        return ResolvedMeta(year="1991", album=t, track="")

    monkeypatch.setattr(resolve, "resolve_metadata", _resolve)
    res = backfill._metadata_pass(str(music))

    # Only the manifest song was processed.
    assert res["year_filled"] == 1
    assert seen["artists"] == ["Chart Artist"]
    assert _read(mani, "date") == "1991"       # manifest song filled
    assert _read(curated, "date") == ""        # curated file untouched


def test_missing_year_retries_after_cooldown_even_with_partial_metadata(library, monkeypatch):
    music, path = library
    clock = [1_000_000.0]
    monkeypatch.setattr(backfill.time, "time", lambda: clock[0])
    monkeypatch.setattr(config, "METADATA_BACKFILL_MAX_TRIES", 2)
    calls = []

    def lookup(artist, title, *, refresh=False):
        calls.append(refresh)
        if refresh:
            return ResolvedMeta(year="1991")
        return ResolvedMeta(album="Real Album")

    monkeypatch.setattr(resolve, "resolve_metadata", lookup)
    backfill._metadata_pass(music)
    backfill._metadata_pass(music)
    assert _read(path, "date") == ""
    assert calls == [False, False]

    backfill._metadata_pass(music)
    clock[0] += 30 * 86400 - 1
    backfill._metadata_pass(music)
    assert calls == [False, False]

    clock[0] += 1
    backfill._metadata_pass(music)
    assert calls == [False, False, True]
    assert _read(path, "date") == "1991"


def test_legacy_settled_missing_year_gets_cooldown(library, monkeypatch):
    music, path = library
    clock = [1_000_000.0]
    monkeypatch.setattr(backfill.time, "time", lambda: clock[0])
    rel = os.path.relpath(path, music)
    backfill._save_meta_state(music, {
        rel: {"done": True, "mtime": backfill._file_mtime(path), "tries": 6}
    })
    calls = []

    def lookup(artist, title, *, refresh=False):
        calls.append(refresh)
        return ResolvedMeta(year="1987")

    monkeypatch.setattr(resolve, "resolve_metadata", lookup)
    backfill._metadata_pass(music)
    assert calls == []
    clock[0] += 30 * 86400
    backfill._metadata_pass(music)
    assert calls == [True]
    assert _read(path, "date") == "1987"


@pytest.mark.parametrize("disabled", [False, True])
def test_due_retry_waits_when_resolver_cannot_run(library, monkeypatch, disabled):
    music, path = library
    clock = [1_000_000.0]
    monkeypatch.setattr(backfill.time, "time", lambda: clock[0])
    rel = os.path.relpath(path, music)
    record = {"done": True, "mtime": backfill._file_mtime(path), "tries": 6,
              "year_retry_after": clock[0]}
    backfill._save_meta_state(music, {rel: record})
    monkeypatch.setattr(resolve, "deezer_budget_available", lambda: False)
    monkeypatch.setattr(resolve, "mb_budget_available", lambda: False)
    if disabled:
        monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", False)
        monkeypatch.setattr(resolve, "deezer_budget_available", lambda: True)
    monkeypatch.setattr(resolve, "resolve_metadata",
                        lambda *a, **kw: pytest.fail("No source budget"))
    backfill._metadata_pass(music)
    assert backfill._load_meta_state(music)[rel] == record


def test_manual_year_is_not_overwritten_at_retry(library, monkeypatch):
    music, path = library
    clock = [1_000_000.0]
    monkeypatch.setattr(backfill.time, "time", lambda: clock[0])
    _tag(path, date="1985", album="Original", tracknumber="1")
    rel = os.path.relpath(path, music)
    backfill._save_meta_state(music, {rel: {
        "done": True, "mtime": backfill._file_mtime(path), "tries": 6,
        "year_retry_after": clock[0],
    }})
    monkeypatch.setattr(resolve, "resolve_metadata",
                        lambda *a, **kw: pytest.fail("Complete file needs no lookup"))
    backfill._metadata_pass(music)
    assert _read(path, "date") == "1985"


def test_report_lists_missing_fields_and_unreadable_files(library, monkeypatch, tmp_path):
    music, path = library
    broken = os.path.join(music, "broken.mp3")
    monkeypatch.setattr(metadata_report, "build_manifest_index",
                        lambda root, strict: {"a": [path, broken]})
    report = metadata_report.build(music)
    assert report["checked"] == 2
    assert report["songs"] == [
        {"path": "A/T.mp3", "artist": "A", "title": "T",
         "missing": ["year", "track"]},
        {"path": "broken.mp3", "unreadable": True},
    ]
    metadata_report.publish(music, str(tmp_path))
    with open(tmp_path / metadata_report.REPORT_NAME, encoding="utf-8") as fh:
        assert json.load(fh)["songs"] == report["songs"]


def test_report_does_not_replace_previous_snapshot_on_invalid_manifest(library, tmp_path):
    music, _ = library
    report_path = tmp_path / metadata_report.REPORT_NAME
    report_path.write_text("previous", encoding="utf-8")
    with open(config.MANIFEST_FILE, "w", encoding="utf-8") as fh:
        json.dump({"entries": []}, fh)
    with pytest.raises(ValueError):
        metadata_report.publish(music, str(tmp_path))
    assert report_path.read_text(encoding="utf-8") == "previous"
