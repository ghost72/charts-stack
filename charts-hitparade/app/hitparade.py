"""
Fetches the Swiss Hitparade (hitparade.ch) and returns the chart songs as a
(position, artist, title) list - source-agnostic, like the Deezer/Last.fm
client in charts-sync.

Technique: The page serves the chart table as HTML (with a browser user agent -
WITHOUT a UA only a placeholder comes back). NO API, NO headless browser
needed. Each chart entry appears as

    <div class="chart_title">
        <a href="/song/..."><b>ARTIST</b><br>TITLE</a>
    </div>

in the HTML. We read the artist from <b>...</b> and the title from the text
after it. The link text is deliberately preferred over the URL path because it
holds the original spelling (with &, ', -) cleanly, whereas the URL replaces
spaces with '-' and thus makes real hyphens indistinguishable.

Robustness: If the page returns 0 songs (rebuild/block/error), a HitparadeError
is raised - the calling service reports this as status=error (dashboard orange
+ Pushover) instead of silently writing an empty playlist.

Charts:
  - singles      -> the official singles Top 100. WORKS (HTML).
  - singles-ch   -> the "CH artist" charts. WARNING: this page loads its table
                    via AJAX/JavaScript afterwards - the HTML holds 0 songs.
                    NOT retrievable with the plain HTTP method (it would need a
                    headless browser). Therefore NOT active by default. The
                    Swiss hits run in the Top 100 anyway.
"""

import html
import logging
import re
from datetime import date

import httpx

from .i18n import t

logger = logging.getLogger(__name__)

_BASE = "https://hitparade.ch/charts"

# Without a real browser user agent hitparade.ch only returns an empty
# placeholder. With a UA the full chart HTML comes back.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "de-CH,de;q=0.9",
}

# Chart entry: <div class="chart_title"> ... <b>ARTIST</b><br>TITLE</a>
# We match the link content: <b>(artist)</b> optional <br> then (title)
# up to the closing </a>.
_ENTRY_RE = re.compile(
    r'<div class="chart_title">.*?<a[^>]*>\s*<b>(?P<artist>.*?)</b>\s*(?:<br\s*/?>)?\s*(?P<title>.*?)\s*</a>',
    re.DOTALL | re.IGNORECASE,
)

# Strip HTML tags inside the captured text (e.g. nested spans).
_TAG_RE = re.compile(r"<[^>]+>")


class HitparadeError(Exception):
    """Parsing failed: page answers but returns 0 songs (markup
    rebuilt/blocked) -> OUR scraper must be repaired (hard, red)."""


class HitparadeUnavailable(HitparadeError):
    """Page temporarily unreachable (HTTP error/timeout/5xx) - this is a
    problem on hitparade.ch, not a scraper defect. Handled more leniently
    (dashboard yellow/idle, no alarm) because it usually recovers on its own."""


def _clean(text: str) -> str:
    """Strip HTML tags + decode HTML entities, trim whitespace."""
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch(chart: str, day: date, timeout_s: float, retries: int) -> str:
    """Load the chart HTML for one day (ISO: dd-mm-yyyy, like the page)."""
    datestr = day.strftime("%d-%m-%Y")
    url = f"{_BASE}/{chart}/{datestr}"
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = httpx.get(url, headers=_HEADERS, timeout=timeout_s,
                          follow_redirects=True)
            r.raise_for_status()
            # hitparade.ch is ISO-8859-1 (Latin-1), not UTF-8. httpx does read
            # the charset from the header, but we set it explicitly so that
            # umlauts/accents (é, à, ü ...) are decoded correctly - otherwise
            # broken characters appear that disturb the library matching.
            if not r.encoding or r.encoding.lower() in ("ascii", "utf-8"):
                r.encoding = "iso-8859-1"
            return r.text
        except Exception as e:  # noqa: BLE001 - collect transport errors
            last_err = e
            logger.warning(t("hitparade.fetch.attempt_failed",
                             url=url, attempt=attempt, retries=retries, error=e))
    # Transport/HTTP error (incl. 5xx from hitparade.ch) -> temporarily
    # unreachable, do NOT treat as a scraper defect.
    raise HitparadeUnavailable(t("hitparade.err.unavailable",
                                 url=url, error=last_err))


def fetch_chart(chart: str, day: date, timeout_s: float = 15.0,
                retries: int = 3) -> list[tuple[int, str, str]]:
    """
    Fetch a Hitparade list and return [(position, artist, title), ...].

    chart: "singles" (Top 100) or "singles-ch" (CH artists).
    day:   chart date (the page uses the Sunday date of the week).

    Raises HitparadeError if the fetch fails OR 0 songs are parsed
    (= page rebuilt/empty -> the monitoring should fire).
    """
    doc = _fetch(chart, day, timeout_s, retries)
    entries: list[tuple[int, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in _ENTRY_RE.finditer(doc):
        artist = _clean(m.group("artist"))
        title = _clean(m.group("title"))
        if not artist or not title:
            continue
        key = (artist.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        entries.append((len(entries) + 1, artist, title))

    if not entries:
        # Hard error signalling: page reachable, but not a single chart
        # entry recognized -> markup changed or empty. This MUST stand out
        # (monitoring), not pass silently as "nothing missing".
        raise HitparadeError(t("hitparade.err.zero_songs", chart=chart))

    logger.info(t("hitparade.fetch.parsed",
                  chart=chart, date=day.strftime("%d.%m.%Y"), count=len(entries)))
    return entries


def latest_sunday(today: date | None = None) -> date:
    """
    Return the date of the most recent Sunday (the Hitparade is updated on
    Sundays; its URL uses the Sunday date of the week). If today is Sunday,
    today is returned.
    """
    d = today or date.today()
    # Python: Monday=0 ... Sunday=6. Days since the last Sunday:
    return d - __import__("datetime").timedelta(days=(d.weekday() + 1) % 7)
