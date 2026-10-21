"""
Tests for slsk.verify_transfer: after an enqueue, confirm whether a provider
actually starts delivering (bytes flowing) or just accepted the request and
stalls ("Queued, Remotely" with 0 bytes) / fails.

Uses a fake slskd client whose download list is scripted per poll, so no
network and no real time waiting (poll interval is 0 and verify window tiny).
All tests run WITHOUT network/Docker.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import slsk


class _FakeDownloads:
    def __init__(self, snapshots):
        # snapshots: list of transfer dicts returned on successive calls; the
        # last one repeats once exhausted.
        self._snapshots = snapshots
        self._i = 0

    def get_all_downloads(self, includeRemoved=False):
        snap = self._snapshots[min(self._i, len(self._snapshots) - 1)]
        self._i += 1
        if snap is None:
            return []
        # Wrap a flat file dict into slskd's user/directory/file structure.
        return [{
            "username": snap["username"],
            "directories": [{"files": [snap]}],
        }]


class _FakeClient:
    def __init__(self, snapshots):
        self.transfers = _FakeDownloads(snapshots)


def _file(state, bytes_done, user="Peer", name="x.mp3"):
    return {"username": user, "filename": name, "state": state,
            "bytesTransferred": bytes_done, "size": 1000, "id": "t1"}


def test_delivering_when_bytes_flow():
    client = _FakeClient([_file("InProgress", 0), _file("InProgress", 512)])
    assert slsk.verify_transfer(client, "Peer", "x.mp3",
                                verify_seconds=5, poll_seconds=0) == "delivering"


def test_delivering_when_inprogress_even_at_zero():
    client = _FakeClient([_file("InProgress", 0)])
    assert slsk.verify_transfer(client, "Peer", "x.mp3",
                                verify_seconds=5, poll_seconds=0) == "delivering"


def test_failed_on_terminal_error_state():
    client = _FakeClient([_file("Queued, Remotely", 0),
                          _file("Completed, Errored", 0)])
    assert slsk.verify_transfer(client, "Peer", "x.mp3",
                                verify_seconds=5, poll_seconds=0) == "failed"


def test_stalled_when_queued_with_zero_bytes_until_deadline():
    # Always queued, never any bytes -> stalls out after the (tiny) window.
    client = _FakeClient([_file("Queued, Remotely", 0)])
    assert slsk.verify_transfer(client, "Peer", "x.mp3",
                                verify_seconds=0.01, poll_seconds=0) == "stalled"


def test_verification_disabled_returns_delivering():
    client = _FakeClient([_file("Queued, Remotely", 0)])
    assert slsk.verify_transfer(client, "Peer", "x.mp3",
                                verify_seconds=0, poll_seconds=0) == "delivering"


def test_succeeded_counts_as_delivering():
    client = _FakeClient([_file("Completed, Succeeded", 1000)])
    assert slsk.verify_transfer(client, "Peer", "x.mp3",
                                verify_seconds=5, poll_seconds=0) == "delivering"
