"""
charts-upgrade Pushover summary: entries are atomic three-line blocks, the
per-run cap keeps its note and the 1024-char budget cuts only between entries.
All tests run WITHOUT network/Docker (notify.send is stubbed).
"""
import pathlib

from app import config, i18n, main, notify_budget

_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _setup(monkeypatch, dry_run):
    monkeypatch.setattr(config, "DRY_RUN", dry_run)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    sent = {}
    monkeypatch.setattr(main.notify, "send",
                        lambda title, msg: sent.update(title=title, msg=msg))
    return sent


def _songs(n, pad=0):
    return [{"artist": f"Artist {i}", "title": f"Song {i}",
             "reason": "R" * pad + "128 kbps -> better", "new_bitrate": 320}
            for i in range(n)]


def test_below_cap_all_entries_shown(monkeypatch):
    sent = _setup(monkeypatch, dry_run=False)
    main._notify_summary([], _songs(3), 10, 0)
    lines = sent["msg"].split("\n")
    assert lines[0] == i18n.t("upgrade.notify.upgraded_header", count=3)
    assert lines[1] == ""
    assert sum(1 for ln in lines if ln.startswith("♪") or "Artist" in ln) == 3
    assert "more" not in sent["msg"]


def test_cap_note_kept_for_per_run_cap(monkeypatch):
    sent = _setup(monkeypatch, dry_run=True)
    main._notify_summary(_songs(18), [], 10, 0)
    lines = sent["msg"].split("\n")
    assert lines[-1] == i18n.t("upgrade.notify.more", count=3)
    assert sum(1 for ln in lines if "Artist" in ln) == 15


def test_fifteen_long_entries_within_budget_never_split(monkeypatch):
    sent = _setup(monkeypatch, dry_run=False)
    main._notify_summary([], _songs(15, pad=90), 10, 0)
    msg = sent["msg"]
    assert notify_budget.measure([msg]) <= 1024
    lines = msg.split("\n")
    artists = [ln for ln in lines if "Artist" in ln]
    reasons = [ln for ln in lines if "RRRR" in ln]
    # Every shown song comes with its reason line (entries are atomic).
    assert len(artists) == len(reasons) > 0
    assert len(artists) < 15
    assert lines[-1] == i18n.t("upgrade.notify.more", count=15 - len(artists))
