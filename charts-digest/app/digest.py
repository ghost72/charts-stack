"""
Sammel- und Gruppierlogik fuer die taegliche Zusammenfassung.

Liest das von charts-import gefuehrte Import-Log (JSON Lines, eine Zeile pro
einsortiertem Song: {ts, source, artist, title}), filtert auf das Rueckblick-
Fenster (Default 24 h) und gruppiert nach Quelle. Dampft das Log dabei auf
LOG_RETENTION_DAYS ein (kompakt neu schreiben), damit es nicht unbegrenzt
waechst.

Vertrauenswuerdig: Datengrundlage ist das, was charts-import beim Import
tatsaechlich geschrieben hat (korrekte source, kanonischer Name) - KEINE
mtime-/Titel-Heuristik, die durch Cover-Backfill/Retag verfaelscht wuerde.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# Emoji + Label-Key je Quelle (source aus dem Manifest/Import-Log). Das Emoji
# taucht vor der Gruppen-Ueberschrift in der Pushover-Meldung auf; das Label
# wird erst zur Laufzeit uebersetzt (t(label_key)), damit CHARTS_LANG greift.
_SOURCE_LABELS = {
    "charts": ("🔥", "digest.source.charts"),
    "mundart": ("🇨🇭", "digest.source.mundart"),
    "flashback": ("📻", "digest.source.flashback"),
    "manuell": ("🙋", "digest.source.manuell"),
    "upgrade": ("⬆️", "digest.source.upgrade"),
    "hitparade": ("🏆", "digest.source.hitparade"),
    "recommend": ("🎧", "digest.source.recommend"),
    "": ("🎵", "digest.source.unknown"),
}


def _parse_ts(s: str) -> float | None:
    """ISO-8601 (mit TZ-Offset) -> Unix-Zeit. None bei Parse-Fehler."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        # Fallback: ohne Offset (sollte nicht vorkommen, aber robust bleiben).
        try:
            return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
        except ValueError:
            return None


def _read_log() -> list[dict]:
    """Liest alle Log-Zeilen (robust gegen kaputte Zeilen)."""
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
    Schreibt das Log kompakt neu und verwirft Eintraege aelter als
    LOG_RETENTION_DAYS. Haelt das Log klein, ohne Datenverlust fuers Fenster.
    Fehler sind nicht fatal (nur Log).
    """
    cutoff = time.time() - config.LOG_RETENTION_DAYS * 86400
    kept = [r for r in records
            if (_parse_ts(r.get("ts", "")) or 0) >= cutoff]
    if len(kept) == len(records):
        return  # nichts zu kuerzen
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
    Sammelt die Importe im Rueckblick-Fenster, gruppiert nach source.
    Rueckgabe: ({source: [eintrag, ...]}, gesamtanzahl). Dampft das Log ein.
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
    # Aufraeumen (nach dem Lesen, damit das Fenster vollstaendig war).
    _prune_log(records)
    return groups, total


def _dedup(entries: list[dict]) -> list[tuple[str, str]]:
    """(artist, title)-Paare, Duplikate entfernt, Reihenfolge erhalten."""
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
    """Kuerzt einen langen Titel am ENDE: "Anfang…".

    Ziel: kuerzere Zeilen, damit mehr Songs ins Pushover-Zeichenbudget passen.
    Der Artist wird NIE gekuerzt - nur der Titel. Es bleibt der ANFANG des
    Titels stehen (an einer Wortgrenze geschnitten, damit nicht mitten im Wort),
    gefolgt von "…". Artist + Songanfang reichen, um den Song zu erkennen.
    max_len <= 0 oder kurzer Titel -> unveraendert zurueck.
    """
    t = title.strip()
    if max_len <= 0 or len(t) <= max_len:
        return t
    ell = "…"
    keep = max_len - len(ell)          # Zeichen fuer den Anfang
    head = t[:keep]
    # An der letzten Wortgrenze schneiden, wenn dadurch nicht zu viel wegfaellt
    # (sonst - z.B. ein einziges sehr langes Wort - hart bei keep).
    space = head.rfind(" ")
    if space >= keep // 2:
        head = head[:space]
    return head.rstrip() + ell


def build_message(groups: dict, total: int) -> "tuple[str, str, int]":
    """
    Baut (titel, nachricht, unique_total) fuer Pushover. Gruppen nach Groesse
    sortiert; pro Gruppe bis MAX_PER_SOURCE Songs, Rest als '... und N weitere'.
    """
    yesterday = (datetime.now() - timedelta(hours=config.DIGEST_WINDOW_HOURS)
                 ).strftime("%d.%m.")
    today = datetime.now().strftime("%d.%m.")

    # pro Quelle deduplizieren und zaehlen
    per_source = []
    unique_total = 0
    for src, entries in groups.items():
        pairs = _dedup(entries)
        if pairs:
            per_source.append((src, pairs))
            unique_total += len(pairs)
    per_source.sort(key=lambda x: len(x[1]), reverse=True)

    # Titel mit Song-Zahl; dezente Variation im Emoji je nach Ausbeute.
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

    # Pushover kappt eine 'message' hart bei PUSHOVER_MESSAGE_LIMIT Zeichen
    # (sonst bricht ein Titel mitten ab und der Footer fehlt). Darum budget-
    # bewusst aufbauen: Der Footer ist RESERVIERT und wird immer angehaengt;
    # laeuft der Body voll, schliessen wir die aktuelle Quelle sauber mit
    # "... und N weitere" ab und brechen den Rest ab. Laenge zaehlt auf dem
    # fertigen (per \n verbundenen) HTML-Rohtext - genau das sieht Pushover.
    limit = config.PUSHOVER_MESSAGE_LIMIT
    footer_text = "\n".join(footer)
    budget = limit - len(footer_text) - 1  # -1 fuer das \n vor dem Footer

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
            # Unbekannte Quelle: Rohwert als Label, sonst uebersetztes Fallback.
            emoji, label = "🎵", src or t("digest.source.unknown")
        shown = pairs[:config.MAX_PER_SOURCE]
        rest = len(pairs) - len(shown)

        # Kopf der Quelle probehalber anhaengen; passt nicht mal der, abbrechen.
        trial = body + ["", t("digest.notify.group_header",
                              emoji=emoji, label=label, count=len(pairs))]
        if _len(trial) > budget:
            truncated = True
            break
        body = trial

        for i, (a, title_txt) in enumerate(shown):
            # Artist bleibt voll, nur der Titel wird bei Bedarf am Ende gekuerzt
            # ("Anfang…") - Artist + Songanfang reichen zur Erkennung. Kompaktes
            # Zeilenformat (keine Einrueckung) spart Platz -> mehr Songs passen.
            song = t("digest.notify.song",
                     artist=a, title=_shorten_title(title_txt, config.TITLE_MAX_LEN))
            remaining = len(shown) - i + (1 if rest else 0)
            # Reserve fuer eine moegliche "... und N weitere"-Zeile einplanen,
            # solange noch Songs/Rest folgen (sonst koennte die Abschlusszeile
            # selbst das Budget sprengen).
            more_hint = t("digest.notify.more", count=rest + (len(shown) - i))
            need_hint = remaining > 1
            projected = _len(body + [song] + ([more_hint] if need_hint else []))
            if projected > budget:
                # Dieser Song passt nicht mehr -> alles ab hier zusammenfassen.
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
