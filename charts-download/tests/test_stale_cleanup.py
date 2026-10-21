"""
Tests for charts-download's slskd stale-transfer housekeeping
(slsk.cleanup_stale_downloads + slsk._parse_requested_at).

cleanup_stale_downloads cancels/removes dead weight from slskd's download list
once per run, independent of the per-song state:
  - failed transfers (Errored/TimedOut/Rejected/...) regardless of age,
  - 0-byte "stuck" transfers (peer accepted but never sent) older than the
    stale threshold,
and leaves succeeded, actively-progressing, and fresh queued transfers alone.

All tests run WITHOUT network/Docker (a fake slskd client records the cancels).
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import slsk


class _FakeTransfers:
    def __init__(self, users):
        self._users = users
        self.cancelled: list[tuple[str, str]] = []

    def get_all_downloads(self, includeRemoved=False):
        return self._users

    def cancel_download(self, username, id, remove):
        self.cancelled.append((username, id))


class _FakeClient:
    def __init__(self, files):
        # Wrap a flat list of file dicts into slskd's user/dir/file structure.
        by_user: dict[str, list] = {}
        for f in files:
            by_user.setdefault(f["username"], []).append(f)
        users = [{"username": u, "directories": [{"files": fs}]}
                 for u, fs in by_user.items()]
        self.transfers = _FakeTransfers(users)


def _iso(hours_ago):
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _f(state, bytes_done, hours_ago, user="U", fid="id", name="x.mp3"):
    return {"username": user, "id": fid, "filename": name, "state": state,
            "bytesTransferred": bytes_done, "requestedAt": _iso(hours_ago)}


def test_failed_transfer_removed_regardless_of_age():
    client = _FakeClient([_f("Completed, Errored", 0, 0.1, fid="e1")])
    n = slsk.cleanup_stale_downloads(client, stale_hours=6)
    assert n == 1
    assert client.transfers.cancelled == [("U", "e1")]


def test_old_stuck_queued_removed():
    client = _FakeClient([_f("Queued, Remotely", 0, 8, fid="q1")])
    n = slsk.cleanup_stale_downloads(client, stale_hours=6)
    assert n == 1
    assert ("U", "q1") in client.transfers.cancelled


def test_fresh_queued_is_left_alone():
    client = _FakeClient([_f("Queued, Remotely", 0, 1, fid="q2")])
    n = slsk.cleanup_stale_downloads(client, stale_hours=6)
    assert n == 0
    assert client.transfers.cancelled == []


def test_succeeded_never_removed():
    client = _FakeClient([_f("Completed, Succeeded", 1000, 48, fid="s1")])
    n = slsk.cleanup_stale_downloads(client, stale_hours=6)
    assert n == 0


def test_in_progress_never_removed_even_if_old():
    # Actively transferring (bytes > 0) must survive regardless of age.
    client = _FakeClient([_f("InProgress", 500, 48, fid="p1")])
    n = slsk.cleanup_stale_downloads(client, stale_hours=6)
    assert n == 0


def test_mixed_batch():
    client = _FakeClient([
        _f("Completed, Succeeded", 1000, 48, fid="ok"),
        _f("Completed, TimedOut", 0, 48, fid="to"),     # failed -> remove
        _f("Queued, Remotely", 0, 10, fid="oldq"),      # old stuck -> remove
        _f("Queued, Remotely", 0, 1, fid="newq"),       # fresh -> keep
        _f("InProgress", 300, 10, fid="prog"),          # progressing -> keep
    ])
    n = slsk.cleanup_stale_downloads(client, stale_hours=6)
    assert n == 2
    removed_ids = {i for _u, i in client.transfers.cancelled}
    assert removed_ids == {"to", "oldq"}


def test_parse_requested_at_handles_slskd_formats():
    # 7 sub-second digits + trailing Z (as slskd emits for startedAt/endedAt).
    ts1 = slsk._parse_requested_at("2026-10-02T19:13:32.1720171Z")
    ts2 = slsk._parse_requested_at("2026-10-02T19:13:32.8147658")
    ts3 = slsk._parse_requested_at("2026-10-02T19:13:32")
    assert ts1 and ts2 and ts3
    # Unparseable -> None (treated as "no age known", not removed).
    assert slsk._parse_requested_at("") is None
    assert slsk._parse_requested_at("garbage") is None
