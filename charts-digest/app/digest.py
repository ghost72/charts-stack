"""
Collection and grouping logic for the daily summary.

Reads the import log kept by charts-import (JSON Lines, one line per filed
song: {ts, source, artist, title}), filters to the look-back window
(default 24 h) and groups by source. In the process it trims the log to
LOG_RETENTION_DAYS (rewrite compactly) so it does not grow unbounded.

Trustworthy: the data basis is what charts-import actually wrote at import time
(correct source, canonical name) - NO mtime/title heuristic that would be
distorted by cover backfill/retag.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# Emoji + label key per source (source from the manifest/import log). The emoji
# appears before the group heading in the Pushover message; the label is only
# translated at runtime (t(label_key)) so CHARTS_LANG takes effect.
_SOURCE_LABELS = {
    "charts": ("🔥", "digest.source.charts"),
    "mundart": ("🇨🇭", "digest.source.mundart"),
    "flashback": ("📻", "digest.source.flashback"),
    "manuell": ("🙋", "digest.source.manuell"),
    "upgrade": ("⬆️", "digest.source.upgrade"),
    "hitparade": ("🏆", "digest.source.hitparade"),
    "recommend": ("🎧", "digest.source.recommend"),
    # charts-energy library-mode downloads: charts-download derives the source
    # from the missing-file name "missing-energy-library.txt" -> "energy-library"
    # (see charts-download/app/missing.py source_label). Label it as Energy here
    # so the daily digest shows a clean "⚡ Energy" group instead of the raw slug.
    "energy-library": ("⚡", "digest.source.energy"),
    "": ("🎵", "digest.source.unknown"),
}


def _parse_ts(s: str) -> float | None:
    """ISO-8601 (with TZ offset) -> Unix time. None on parse error."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        # Fallback: without offset (should not happen, but stay robust).
        try:
            return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
        except ValueError:
            return None


def _read_log() -> list[dict]:
    """Reads all log lines (robust against broken lines)."""
    path = config.IMPORT_LOG_FILE
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        out.append(rec)
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, OSError):
        pass
    return out


def _prune_log(records: list[dict]) -> None:
    """
    Rewrites the log compactly and discards entries older than
    LOG_RETENTION_DAYS. Keeps the log small without data loss for the window.
    Errors are not fatal (log only).
    """
    cutoff = time.time() - config.LOG_RETENTION_DAYS * 86400
    kept = [r for r in records
            if (_parse_ts(r.get("ts", "")) or 0) >= cutoff]
    if len(kept) == len(records):
        return  # nothing to trim
    path = config.IMPORT_LOG_FILE
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in kept:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        logger.info(t("digest.log.pruned", before=len(records), after=len(kept),
                      days=config.LOG_RETENTION_DAYS))
    except OSError as e:
        logger.debug(t("digest.log.prune_failed", error=e))


def collect() -> "tuple[dict[str, list[dict]], int]":
    """
    Collects the imports in the look-back window, grouped by source.
    Return: ({source: [entry, ...]}, total count). Trims the log.
    """
    records = _read_log()
    cutoff = time.time() - config.DIGEST_WINDOW_HOURS * 3600
    groups: dict[str, list[dict]] = {}
    total = 0
    for r in records:
        ts = _parse_ts(r.get("ts", ""))
        if ts is None or ts < cutoff:
            continue
        src = (r.get("source") or "").strip()
        groups.setdefault(src, []).append(r)
        total += 1
    # Clean up (after reading, so the window was complete).
    _prune_log(records)
    return groups, total


def _dedup(entries: list[dict]) -> list[tuple[str, str]]:
    """(artist, title) pairs, duplicates removed, order preserved."""
    out = []
    seen = set()
    for e in entries:
        a = (e.get("artist") or "").strip()
        t = (e.get("title") or "").strip()
        key = f"{a.lower()}|{t.lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append((a, t))
    return out


def _shorten_title(title: str, max_len: int) -> str:
    """Shortens a long title at the END: "start…".

    Goal: shorter lines so more songs fit the Pushover character budget. The
    artist is NEVER shortened - only the title. The START of the title is kept
    (cut at a word boundary so not mid-word), followed by "…". Artist + title
    start are enough to recognize the song.
    max_len <= 0 or short title -> returned unchanged.
    """
    t = title.strip()
    if max_len <= 0 or len(t) <= max_len:
        return t
    ell = "…"
    keep = max_len - len(ell)          # characters for the start
    head = t[:keep]
    # Cut at the last word boundary if not too much is lost that way (otherwise
    # - e.g. a single very long word - hard at keep).
    space = head.rfind(" ")
    if space >= keep // 2:
        head = head[:space]
    return head.rstrip() + ell


def build_message(groups: dict, total: int) -> "tuple[str, str, int]":
    """
    Builds (title, message, unique_total) for Pushover. Groups sorted by size;
    per group up to MAX_PER_SOURCE songs, rest as '... and N more'.
    """
    yesterday = (datetime.now() - timedelta(hours=config.DIGEST_WINDOW_HOURS)
                 ).strftime("%d.%m.")
    today = datetime.now().strftime("%d.%m.")

    # deduplicate and count per source
    per_source = []
    unique_total = 0
    for src, entries in groups.items():
        pairs = _dedup(entries)
        if pairs:
            per_source.append((src, pairs))
            unique_total += len(pairs)
    per_source.sort(key=lambda x: len(x[1]), reverse=True)

    # Title with song count; subtle variation in the emoji depending on yield.
    if unique_total >= 20:
        head_emoji = "🚀"
    elif unique_total >= 5:
        head_emoji = "🎉"
    else:
        head_emoji = "🎶"
    title = t("digest.notify.title", emoji=head_emoji, count=unique_total)

    header = [t("digest.notify.header_period", period_from=yesterday, period_to=today),
              t("digest.notify.header_count", count=unique_total,
                sources=len(per_source)),
              "➖➖➖➖➖➖➖➖"]
    footer = ["", t("digest.notify.footer")]

    # Pushover truncates a 'message' hard at PUSHOVER_MESSAGE_LIMIT characters
    # (otherwise a title breaks off mid-way and the footer is missing). So build
    # budget-aware: the footer is RESERVED and always appended; if the body
    # fills up, we close the current source cleanly with "... and N more" and
    # cut off the rest. Length counts on the finished (\n-joined) HTML raw text
    # - exactly what Pushover sees.
    limit = config.PUSHOVER_MESSAGE_LIMIT
    footer_text = "\n".join(footer)
    budget = limit - len(footer_text) - 1  # -1 for the \n before the footer

    body = list(header)

    def _len(ls):
        return len("\n".join(ls))

    truncated = False
    for src, pairs in per_source:
        if truncated:
            break
        label_entry = _SOURCE_LABELS.get(src)
        if label_entry is not None:
            emoji, label = label_entry[0], t(label_entry[1])
        else:
            # Unknown source: raw value as label, otherwise translated fallback.
            emoji, label = "🎵", src or t("digest.source.unknown")
        shown = pairs[:config.MAX_PER_SOURCE]
        rest = len(pairs) - len(shown)

        # Append the source header as a trial; if even that does not fit, stop.
        trial = body + ["", t("digest.notify.group_header",
                              emoji=emoji, label=label, count=len(pairs))]
        if _len(trial) > budget:
            truncated = True
            break
        body = trial

        for i, (a, title_txt) in enumerate(shown):
            # Artist stays full, only the title is shortened at the end if
            # needed ("start…") - artist + title start are enough to recognize
            # it. Compact line format (no indentation) saves space -> more songs
            # fit.
            song = t("digest.notify.song",
                     artist=a, title=_shorten_title(title_txt, config.TITLE_MAX_LEN))
            remaining = len(shown) - i + (1 if rest else 0)
            # Reserve space for a possible "... and N more" line as long as more
            # songs/rest follow (otherwise the closing line itself could blow
            # the budget).
            more_hint = t("digest.notify.more", count=rest + (len(shown) - i))
            need_hint = remaining > 1
            projected = _len(body + [song] + ([more_hint] if need_hint else []))
            if projected > budget:
                # This song no longer fits -> summarize everything from here.
                left = rest + (len(shown) - i)
                body.append(t("digest.notify.more", count=left))
                truncated = True
                break
            body.append(song)
        else:
            if rest:
                body.append(t("digest.notify.more", count=rest))

    body.extend(footer)
    return title, "\n".join(body).rstrip(), unique_total
