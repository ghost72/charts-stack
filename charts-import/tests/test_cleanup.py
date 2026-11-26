"""Regression tests for cleanup in the shared downloads and state directory."""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import cleanup, config


@pytest.mark.parametrize("keep,retention", [(1, 0), (0, 1)])
def test_download_cleanup_preserves_system_data_and_quarantine(tmp_path, monkeypatch, keep, retention):
    downloads = tmp_path / "Charts"
    music = tmp_path / "Music"
    rejected = downloads / "_rejected"
    monkeypatch.setattr(config, "DOWNLOADS_DIR", str(downloads))
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "REJECTED_DIR", str(rejected))
    monkeypatch.setattr(config, "KEEP_LATEST", keep)
    monkeypatch.setattr(config, "RETENTION_DAYS", retention)
    monkeypatch.setattr(config, "TRASH_RETENTION_DAYS", 0)
    monkeypatch.setattr(config, "REJECTED_RETENTION_DAYS", 0)
    monkeypatch.setattr(cleanup.time, "time", lambda: 1_000_000)

    def create(name, mtime):
        path = downloads / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("data")
        os.utime(path, (mtime, mtime))
        return path

    old_audio = create("artist/old.mp3", 1)
    new_audio = create("artist/new.FLAC", 999_999)
    protected = [
        create(".charts-artist-selection.json", 1),
        create(".charts-manifest.json", 1),
        create(".charts-artist-selection.lock", 1),
        create("missing-mundart.txt", 1),
        create(".charts-status/charts-sync.json", 1),
        create(".private/old.mp3", 1),
        create("_rejected/old.mp3", 1),
        create("artist/cover.jpg", 1),
    ]

    assert cleanup.cleanup_downloads() == [str(old_audio)]
    assert not old_audio.exists()
    assert new_audio.exists()
    assert all(path.exists() for path in protected)


def test_quarantine_retention_still_removes_expired_files(tmp_path, monkeypatch):
    rejected = tmp_path / "_rejected"
    rejected.mkdir()
    old = rejected / "old.mp3"
    old.write_text("data")
    os.utime(old, (1, 1))
    monkeypatch.setattr(config, "REJECTED_DIR", str(rejected))
    monkeypatch.setattr(cleanup.time, "time", lambda: 1_000_000)

    assert cleanup.cleanup_rejected(1) == 1
    assert not old.exists()
