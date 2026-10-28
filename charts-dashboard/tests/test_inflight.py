"""
Tests for the "In progress" pipeline overview (charts-dashboard).

Covers the four honestly-labelled panels end to end, all WITHOUT network or
Docker (mirrors the test_i18n.py contract):
  - the slskd transfer -> song-name join (panel 1),
  - the missing*.txt parser + dedupe + per-source aggregation (panel 2),
  - the _rejected mtime-sort + preview cap (panel 3),
  - the upgrade/import field extraction (panel 4 + run message),
  - collect() integration + graceful degradation when slskd is down,
  - the snapshot TTL cache honoured while `active` is rebuilt every call,
  - the served HTML containing the new section + four panels + empty-state keys,
  - i18n parity for the new dashboard.ui.inflight.* namespace.
"""

import importlib
import json
import pathlib

import pytest

from app import collector, config, i18n


# Central shared lang files: app -> charts-dashboard -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


# --- Panel 1: transfer -> song-name join -----------------------------------

def _transfers(files_by_user):
    """Build a slskd-shaped transfers list: {user: [file dict, ...]}."""
    return [
        {"username": u, "directories": [{"files": files}]}
        for u, files in files_by_user.items()
    ]


def test_join_matched_and_unmatched():
    transfers = _transfers({
        "peer1": [
            {"state": "InProgress", "filename": "music\\Remi Wolf\\Bottle.mp3",
             "size": 100, "bytesTransferred": 42},
            {"state": "Queued, Remotely", "filename": "x\\y\\sampler-track.mp3"},
        ],
    })
    requested = {
        "remi wolf|bottle": {"artist": "Remi Wolf", "title": "Bottle",
                             "username": "peer1",
                             "filename": "music\\Remi Wolf\\Bottle.mp3"},
    }
    out = collector._inflight_join_active(transfers, requested, 25)
    assert out["ok"] is True
    assert out["count"] == 2
    assert out["truncated"] is False
    matched, unmatched = out["items"][0], out["items"][1]
    # matched -> clean "Artist – Title" label, pct computed from size/bytes
    assert matched["matched"] is True
    assert matched["label"] == "Remi Wolf \u2013 Bottle"
    assert matched["pct"] == 42
    # unmatched -> basename of the Windows-style path, no pct (no size)
    assert unmatched["matched"] is False
    assert unmatched["label"] == "sampler-track.mp3"
    assert unmatched["pct"] is None


def test_join_skips_completed_and_cancelled():
    transfers = _transfers({
        "p": [
            {"state": "Completed, Succeeded", "filename": "a.mp3"},
            {"state": "Completed, Cancelled", "filename": "b.mp3"},
            {"state": "InProgress", "filename": "c.mp3"},
        ],
    })
    out = collector._inflight_join_active(transfers, {}, 25)
    assert out["count"] == 1
    assert out["items"][0]["label"] == "c.mp3"


def test_join_pct_zero_size_is_none():
    transfers = _transfers({"p": [
        {"state": "InProgress", "filename": "a.mp3", "size": 0, "bytesTransferred": 10},
    ]})
    out = collector._inflight_join_active(transfers, {}, 25)
    assert out["items"][0]["pct"] is None


def test_join_truncates_and_keeps_true_count():
    files = [{"state": "InProgress", "filename": f"f{i}.mp3"} for i in range(30)]
    transfers = _transfers({"p": files})
    out = collector._inflight_join_active(transfers, {}, 25)
    assert out["count"] == 30
    assert len(out["items"]) == 25
    assert out["truncated"] is True


# --- Panel 2: missing*.txt parse / dedupe / per-source ----------------------

def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_missing_parse_dedupe_and_sources(tmp_path):
    _write(tmp_path / "missing.txt", [
        "# Fehlende Chart-Songs (Stand ...)",
        "",
        "  1. Remi Wolf - Bottle",
        "  2. Ariana Grande - twilight zone",
        "  3. no separator here",      # skipped (no " - ")
    ])
    _write(tmp_path / "missing-energy-library.txt", [
        "  1. Remi Wolf - Bottle",     # cross-file duplicate -> deduped in total
        "  2. Someone - Else",
    ])
    paths = sorted(str(p) for p in tmp_path.glob("missing*.txt"))
    out = collector._inflight_parse_missing(paths, 8)
    # 3 unique songs across both files (Bottle counted once)
    assert out["total"] == 3
    per = {d["source"]: d["count"] for d in out["per_source"]}
    assert per == {"charts": 2, "energy-library": 2}
    # preview in file order, first file first
    assert out["preview"][0] == {"artist": "Remi Wolf", "title": "Bottle"}


def test_missing_unreadable_file_skipped(tmp_path):
    _write(tmp_path / "missing.txt", ["  1. A - B"])
    paths = [str(tmp_path / "missing.txt"), str(tmp_path / "does-not-exist.txt")]
    out = collector._inflight_parse_missing(paths, 8)
    assert out["total"] == 1


# --- Panel 3: _rejected listing --------------------------------------------

def test_rejected_entries_sorted_and_junk_skipped(tmp_path):
    import os
    import time as _t
    rej = tmp_path / "_rejected"
    rej.mkdir()
    (rej / "old.mp3").write_bytes(b"x")
    (rej / "new.mp3").write_bytes(b"x")
    (rej / ".DS_Store").write_bytes(b"x")   # junk, must be skipped
    # make "new.mp3" the most recent
    now = _t.time()
    os.utime(rej / "old.mp3", (now - 100, now - 100))
    os.utime(rej / "new.mp3", (now, now))
    count, preview = collector._inflight_rejected_entries(str(rej), 8)
    assert count == 2
    assert [p["name"] for p in preview] == ["new.mp3", "old.mp3"]


def test_rejected_missing_dir(tmp_path):
    count, preview = collector._inflight_rejected_entries(
        str(tmp_path / "nope"), 8)
    assert count == 0 and preview == []


def test_rejected_preview_cap(tmp_path):
    rej = tmp_path / "_rejected"
    rej.mkdir()
    for i in range(12):
        (rej / f"f{i}.mp3").write_bytes(b"x")
    count, preview = collector._inflight_rejected_entries(str(rej), 8)
    assert count == 12
    assert len(preview) == 8


# --- Panel 4 + import message: field extraction -----------------------------

def test_upgrade_fields_present():
    services = {"charts-upgrade": {
        "status": "ok", "last_run_end": "2026-10-28T04:12:00+0100",
        "next_run": "2026-10-29T04:00:00+0100",
        "counters": {"scan_seen": 1830, "scan_total": 42117},
        "totals": {"upgraded": 7},
    }}
    out = collector._inflight_upgrade_fields(services)
    assert out["available"] is True
    assert out["scan_seen"] == 1830 and out["scan_total"] == 42117
    assert out["upgraded"] == 7
    assert out["status"] == "ok"


def test_upgrade_fields_absent():
    out = collector._inflight_upgrade_fields({})
    assert out["available"] is False
    assert out["scan_seen"] is None and out["upgraded"] is None


def test_import_message_and_counter():
    services = {"charts-import": {"message": "2 rejected (upsampled)",
                                  "counters": {"rejected": 2}}}
    msg, counter = collector._inflight_import_message(services)
    assert msg == "2 rejected (upsampled)"
    assert counter == 2


def test_import_message_absent():
    msg, counter = collector._inflight_import_message({})
    assert msg == "" and counter is None


# --- collect() integration + graceful degradation ---------------------------

def _reset_inflight_cache():
    collector._inflight_cache = None
    collector._inflight_ts = 0.0


def test_collect_inflight_graceful_when_slskd_down(monkeypatch, tmp_path):
    _reset_inflight_cache()

    def _boom(*a, **k):
        raise RuntimeError("no slskd")

    monkeypatch.setattr(collector.httpx, "get", _boom)
    monkeypatch.setattr(config, "SLSKD_API_KEY", "dummy-key")
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "STATUS_DIR", str(tmp_path / ".charts-status"))
    out = collector.collect()
    ip = out["inflight"]
    # all four sub-objects present, active degraded to an honest "not ok"
    assert set(ip) >= {"active", "missing", "rejected", "upgrade"}
    assert ip["active"]["ok"] is False
    assert ip["missing"]["available"] is False   # no missing*.txt in tmp
    assert ip["rejected"]["count"] == 0
    assert ip["upgrade"]["available"] is False


def test_inflight_snapshot_ttl_cached_while_active_rebuilt(monkeypatch, tmp_path):
    _reset_inflight_cache()
    monkeypatch.setattr(config, "SLSKD_API_KEY", "")  # active -> trivial, uncached
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    _write(tmp_path / "missing.txt", ["  1. A - B"])

    calls = {"n": 0}
    real = collector._inflight_snapshots

    def _counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(collector, "_inflight_snapshots", _counting)
    # TTL long enough that two back-to-back calls share the cache
    monkeypatch.setattr(config, "INFLIGHT_TTL", 300)

    collector._inflight({})
    collector._inflight({})
    assert calls["n"] == 1   # snapshots computed once, second call used the cache


# --- Served HTML (TestClient) -----------------------------------------------

def _client(monkeypatch, lang="en"):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    monkeypatch.setenv("DASHBOARD_USERNAME", "admin")
    monkeypatch.setenv("CHARTS_LANG", lang)
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang=lang, lang_dir=_LANG_DIR)
    import app.main as main_mod
    importlib.reload(main_mod)
    main_mod._sessions["test-token"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "test-token")
    return client


def test_served_html_has_inprogress_section(monkeypatch):
    html = _client(monkeypatch).get("/").text
    assert 'id="inprogress"' in html
    assert 'data-i18n="section.inprogress"' in html
    # the render function exists
    assert "function renderInProgress" in html


def test_served_html_ships_inflight_keys(monkeypatch):
    import re
    html = _client(monkeypatch).get("/").text
    m = re.search(r"const I18N = (\{.*\});\s*$", html, re.M)
    assert m
    i18n_obj = json.loads(m.group(1))
    # the four panels' empty-state keys are shipped to the client
    for key in ("inflight.active.none", "inflight.missing.none",
                "inflight.rejected.none", "inflight.upgrade.none",
                "section.inprogress", "inflight.badge.live"):
        assert key in i18n_obj, f"missing client key {key}"


# --- i18n parity for the new namespace --------------------------------------

def _load(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def test_inflight_namespace_parity():
    per_lang = {c: {k for k in _load(c)
                    if k.startswith("dashboard.ui.inflight.")
                    or k == "dashboard.ui.section.inprogress"
                    or k.startswith("dashboard.inprogress.")}
                for c in _SHIPPED}
    assert per_lang["en"] == per_lang["de"] == per_lang["fr"]
    # the namespace actually got populated (not an empty match)
    assert "dashboard.ui.inflight.active.title" in per_lang["en"]
    assert "dashboard.inprogress.log.slskd_failed" in per_lang["en"]


@pytest.fixture(autouse=True)
def _restore():
    yield
    i18n.setup()
    _reset_inflight_cache()
