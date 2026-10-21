"""
Tests for the charts-songrequest provider blocklist.

A provider that accepts a download but never delivers is remembered here for
PROVIDER_BLOCK_DAYS and skipped on future requests. Entries expire so a peer
gets a fresh chance later. All operations fail open and run WITHOUT
network/Docker (the blocklist file is pointed at a tmp path).
"""

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import blocklist, config


def _at(tmp_path, monkeypatch, days=14):
    monkeypatch.setattr(config, "BLOCKLIST_FILE", str(tmp_path / ".songrequest-blocked.json"))
    monkeypatch.setattr(config, "PROVIDER_BLOCK_DAYS", days)


def test_block_then_blocked(tmp_path, monkeypatch):
    _at(tmp_path, monkeypatch)
    blocklist.block("BadPeer")
    assert "BadPeer" in blocklist.blocked_users()


def test_expired_entry_is_ignored_and_pruned(tmp_path, monkeypatch):
    _at(tmp_path, monkeypatch)
    now = time.time()
    # Write an entry that expired an hour ago.
    path = pathlib.Path(config.BLOCKLIST_FILE)
    path.write_text(json.dumps({"blocked": {"OldPeer": now - 3600}}), encoding="utf-8")
    assert blocklist.blocked_users(now=now) == set()
    # Pruned from the file.
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "OldPeer" not in data["blocked"]


def test_active_entry_survives(tmp_path, monkeypatch):
    _at(tmp_path, monkeypatch)
    now = time.time()
    path = pathlib.Path(config.BLOCKLIST_FILE)
    path.write_text(json.dumps({"blocked": {"GoodBlock": now + 3600}}), encoding="utf-8")
    assert "GoodBlock" in blocklist.blocked_users(now=now)


def test_disabled_when_days_zero(tmp_path, monkeypatch):
    _at(tmp_path, monkeypatch, days=0)
    blocklist.block("Whoever")          # no-op
    assert blocklist.blocked_users() == set()


def test_missing_file_is_empty(tmp_path, monkeypatch):
    _at(tmp_path, monkeypatch)
    # No file written yet.
    assert blocklist.blocked_users() == set()


def test_block_sets_ttl_days(tmp_path, monkeypatch):
    _at(tmp_path, monkeypatch, days=14)
    now = 1_000_000.0
    blocklist.block("Peer", now=now)
    data = json.loads(pathlib.Path(config.BLOCKLIST_FILE).read_text(encoding="utf-8"))
    until = data["blocked"]["Peer"]
    assert abs(until - (now + 14 * 86400)) < 1
