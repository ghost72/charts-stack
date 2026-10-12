"""
Holt die Schweizer Hitparade (hitparade.ch) und liefert die Chart-Songs als
(position, artist, title)-Liste - quellenunabhaengig, wie der Deezer-/Last.fm-
Client in charts-sync.

Technik: Die Seite liefert die Chart-Tabelle als HTML aus (mit einem Browser-
User-Agent - OHNE UA kommt nur ein Platzhalter zurueck). KEINE API, KEIN
Headless-Browser noetig. Jeder Chart-Eintrag steht als

    <div class="chart_title">
        <a href="/song/..."><b>ARTIST</b><br>TITEL</a>
    </div>

im HTML. Wir lesen Artist aus <b>...</b> und den Titel aus dem Text dahinter.
Der Link-Text wird bewusst dem URL-Pfad vorgezogen, weil er die Original-
Schreibweise (mit &, ', -) sauber enthaelt, waehrend die URL Leerzeichen durch
'-' ersetzt und so echte Bindestriche nicht mehr unterscheidbar macht.

Robustheit: Liefert die Seite 0 Songs (Umbau/Blockade/Fehler), wird eine
HitparadeError geworfen - der aufrufende Dienst meldet das als status=error
(Dashboard orange + Pushover), statt still eine leere Playlist zu schreiben.

Charts:
  - singles      -> die offizielle Single-Top-100. FUNKTIONIERT (HTML).
  - singles-ch   -> die "CH-Artist"-Charts. ACHTUNG: Diese Seite laedt ihre
                    Tabelle per AJAX/JavaScript nach - im HTML stehen 0 Songs.
                    Mit der reinen HTTP-Methode NICHT abrufbar (es braeuchte
                    einen Headless-Browser). Daher per Default NICHT aktiv.
                    Die Schweizer Hits laufen ohnehin in der Top-100 mit.
"""

import html
import logging
import re
from datetime import date

import httpx

from .i18n import t

logger = logging.getLogger(__name__)

_BASE = "https://hitparade.ch/charts"

# Ohne echten Browser-User-Agent liefert hitparade.ch nur einen leeren
# Platzhalter. Mit UA kommt das vollstaendige Chart-HTML.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "de-CH,de;q=0.9",
}

# Chart-Eintrag: <div class="chart_title"> ... <b>ARTIST</b><br>TITEL</a>
# Wir matchen den Link-Inhalt: <b>(artist)</b> optionales <br> dann (titel)
# bis zum schliessenden </a>.
_ENTRY_RE = re.compile(
    r'<div class="chart_title">.*?<a[^>]*>\s*<b>(?P<artist>.*?)</b>\s*(?:<br\s*/?>)?\s*(?P<title>.*?)\s*</a>',
    re.DOTALL | re.IGNORECASE,
)

# HTML-Tags innerhalb des erfassten Textes entfernen (z.B. verschachtelte spans).
_TAG_RE = re.compile(r"<[^>]+>")


class HitparadeError(Exception):
    """Parsing fehlgeschlagen: Seite antwortet, liefert aber 0 Songs (Markup
    umgebaut/blockiert) -> UNSER Scraper muss repariert werden (hart, rot)."""


class HitparadeUnavailable(HitparadeError):
    """Seite voruebergehend nicht erreichbar (HTTP-Fehler/Timeout/5xx) - das ist
    ein Problem von hitparade.ch, kein Scraper-Defekt. Wird milder behandelt
    (Dashboard gelb/idle, kein Alarm), weil es sich meist von selbst erholt."""


def _clean(text: str) -> str:
    """Entfernt HTML-Tags + dekodiert HTML-Entities, trimmt Whitespace."""
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch(chart: str, day: date, timeout_s: float, retries: int) -> str:
    """Laedt das Chart-HTML fuer einen Tag (ISO: dd-mm-yyyy, wie die Seite)."""
    datestr = day.strftime("%d-%m-%Y")
    url = f"{_BASE}/{chart}/{datestr}"
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = httpx.get(url, headers=_HEADERS, timeout=timeout_s,
                          follow_redirects=True)
            r.raise_for_status()
            # hitparade.ch ist ISO-8859-1 (Latin-1), nicht UTF-8. httpx liest
            # den Charset zwar aus dem Header, aber wir setzen ihn explizit,
            # damit Umlaute/Akzente (é, à, ü ...) korrekt dekodiert werden -
            # sonst entstehen kaputte Zeichen, die das Library-Matching stoeren.
            if not r.encoding or r.encoding.lower() in ("ascii", "utf-8"):
                r.encoding = "iso-8859-1"
            return r.text
        except Exception as e:  # noqa: BLE001 - Transportfehler sammeln
            last_err = e
            logger.warning(t("hitparade.fetch.attempt_failed",
                             url=url, attempt=attempt, retries=retries, error=e))
    # Transport-/HTTP-Fehler (inkl. 5xx von hitparade.ch) -> voruebergehend
    # nicht erreichbar, NICHT als Scraper-Defekt werten.
    raise HitparadeUnavailable(t("hitparade.err.unavailable",
                                 url=url, error=last_err))


def fetch_chart(chart: str, day: date, timeout_s: float = 15.0,
                retries: int = 3) -> list[tuple[int, str, str]]:
    """
    Holt eine Hitparade-Liste und liefert [(position, artist, title), ...].

    chart: "singles" (Top-100) oder "singles-ch" (CH-Artists).
    day:   Chart-Datum (die Seite nutzt das Sonntagsdatum der Woche).

    Wirft HitparadeError, wenn der Abruf fehlschlaegt ODER 0 Songs geparst
    werden (= Seite umgebaut/leer -> das Monitoring soll anschlagen).
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
        # Harte Fehlersignalisierung: Seite erreichbar, aber kein einziger
        # Chart-Eintrag erkannt -> Markup geaendert oder leer. Das MUSS
        # auffallen (Monitoring), nicht still als "nichts fehlt" durchgehen.
        raise HitparadeError(t("hitparade.err.zero_songs", chart=chart))

    logger.info(t("hitparade.fetch.parsed",
                  chart=chart, date=day.strftime("%d.%m.%Y"), count=len(entries)))
    return entries


def latest_sunday(today: date | None = None) -> date:
    """
    Liefert das Datum des juengsten Sonntags (die Hitparade wird sonntags
    aktualisiert; ihre URL nutzt das Sonntagsdatum der Woche). Ist heute
    Sonntag, wird heute zurueckgegeben.
    """
    d = today or date.today()
    # Python: Montag=0 ... Sonntag=6. Tage seit letztem Sonntag:
    return d - __import__("datetime").timedelta(days=(d.weekday() + 1) % 7)
