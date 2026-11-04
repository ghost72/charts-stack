"""
Pushover message budget: assemble a list-shaped message so that it never
exceeds Pushover's 1024-character limit and the head (header + overview line)
is never cut.

A message has three parts:

  * head  - lines that are always kept in full (header, one overview line),
  * blocks - detail lists made of atomic entries; the budget cuts ONLY between
             whole entries, never in the middle of an entry,
  * tail  - lines that are always kept in full (e.g. a footer).

When entries are omitted (budget or a deliberate per-list cap), exactly one
global "... and N more" line is added. Length is measured on the text exactly
as Pushover receives it, i.e. HTML-escaped like ``notify.send`` does.

Pure stdlib, no config/i18n import: the caller passes already translated
strings. This file is a shared helper that is deliberately duplicated into
every service that sends list-shaped Pushover messages (copy-convention, no
cross-service import); a repo-root parity test keeps all copies byte-identical.
"""

from dataclasses import dataclass, field
from typing import Callable

LIMIT = 1024
OVERVIEW_MAX = 360  # chars; see overview_line() fallbacks

_ALLOWED_TAGS = ["b", "i", "u"]


def escaped(text: str) -> str:
    """Escape like ``notify._escape_html_keep_tags`` (what Pushover receives)."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for tag in _ALLOWED_TAGS:
        text = text.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        text = text.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return text


def measure(lines: list[str]) -> int:
    """Length of the joined lines as transmitted (HTML-escaped)."""
    return len(escaped("\n".join(lines)))


def hard_cut(text: str, limit: int = LIMIT) -> str:
    """Last resort: cut raw text so that the escaped result plus "..." fits.

    Raw characters are dropped (not escaped ones), so an entity or a tag can
    never be split.
    """
    if limit <= 0:
        return ""
    if measure([text]) <= limit:
        return text
    cut = text
    while cut and measure([cut + "…"]) > limit:
        cut = cut[:-1]
    return cut + "…"


def overview_line(parts: list[str], compact_parts: list[str] | None = None,
                  sep: str = " · ") -> str:
    """Render already translated overview parts as the overview block.
    Layout rule (uniform across services): a single part is OMITTED (returns
    "") because the detail block below already carries the same numbers; 2
    parts stay on one line joined with ``sep``; 3 or more parts are printed one
    per line. The result may therefore contain newlines; callers append it to
    the head as one item and skip it when it is empty.
    ``compact_parts`` must have the same length and order as ``parts``; a
    length mismatch is ignored. If the full layout is longer than OVERVIEW_MAX
    the compact variant is used, joined on ONE line with ``sep``; if that is
    still too long (or none was given), the leading parts that fit are kept and
    the block ends with an ellipsis. Empty or single ``parts`` return "".
    """
    if len(parts) < 2:
        return ""
    multiline = len(parts) >= 3
    joiner = "\n" if multiline else sep
    suffix = "\n…" if multiline else " …"
    line = joiner.join(parts)
    if len(line) <= OVERVIEW_MAX:
        return line
    use = parts
    if compact_parts is not None and len(compact_parts) == len(parts):
        use, joiner, suffix = compact_parts, sep, " …"
        line = joiner.join(use)
        if len(line) <= OVERVIEW_MAX:
            return line
    kept: list[str] = []
    for part in use:
        candidate = joiner.join(kept + [part])
        if len(candidate) + len(suffix) > OVERVIEW_MAX:
            break
        kept.append(part)
    if not kept:
        return use[0][: OVERVIEW_MAX - len(suffix)] + suffix
    return joiner.join(kept) + suffix


@dataclass
class Entry:
    # One atomic entry (e.g. a song line, or song + reason + blank line).
    lines: list[str] = field(default_factory=list)
    # How many "items" it counts for in the "... and N more" line; 0 for pure
    # info lines that must not be counted.
    weight: int = 1


@dataclass
class Block:
    entries: list[Entry] = field(default_factory=list)
    # Lines printed before the first shown entry (only if one is shown).
    header: list[str] = field(default_factory=list)
    # Items beyond a deliberate cap that were never turned into entries.
    hidden: int = 0
    # Printed after the last entry iff the block is fully shown and hidden > 0.
    # "" => the hidden count is reported through the global "more" line.
    cap_note: str = ""


def fit_message(head: list[str], blocks: list[Block], tail: list[str],
                more: Callable[[int], str], limit: int = LIMIT,
                more_sep: tuple[str, ...] = ()) -> str:
    """Assemble head + blocks + tail within ``limit`` (escaped length).

    Entries are added in block order until the first one that does not fit;
    the shown list is therefore a strict prefix. ``more_sep`` lines are printed
    before the global ``more`` line whenever that line is printed.
    """
    if limit <= 0:
        return ""

    def assemble(shown: list[int]) -> list[str]:
        out = list(head)
        omitted = 0
        for block, n in zip(blocks, shown):
            full = n == len(block.entries)
            note_printed = bool(n > 0 and full and block.hidden > 0
                                and block.cap_note)
            if n > 0:
                out.extend(block.header)
                for entry in block.entries[:n]:
                    out.extend(entry.lines)
                if note_printed:
                    out.append(block.cap_note)
            omitted += sum(e.weight for e in block.entries[n:])
            if not note_printed:
                omitted += block.hidden
        if omitted > 0:
            out.extend(more_sep)
            out.append(more(omitted))
        out.extend(tail)
        return out

    shown = [0] * len(blocks)
    if measure(assemble(shown)) > limit:
        return hard_cut("\n".join(head + tail), limit)
    done = False
    for i, block in enumerate(blocks):
        for _ in block.entries:
            shown[i] += 1
            if measure(assemble(shown)) > limit:
                shown[i] -= 1
                done = True
                break
        if done:
            break
    return "\n".join(assemble(shown)).rstrip()
