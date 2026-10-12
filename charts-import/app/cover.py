"""
Cover-Beschaffung ueber iTunes/Apple (primaer) und Deezer (Fallback).

charts-import kennt zu jedem (Manifest-)Song den sauberen Kuenstler + Titel.
Damit sucht es das passende Cover ueber offene Such-APIs (kein API-Key noetig)
und bettet das Bild in die MP3 ein.

Quellen-Reihenfolge steuerbar ueber COVER_SOURCES (Default: itunes,deezer).
iTunes hat den groessten Katalog mit dedizierten Single-Artworks in hoher
Aufloesung (immer quadratisch); Deezer springt ein, wenn iTunes nichts findet.

Die Trefferauswahl bevorzugt bewusst die echte Single/das Original-Album und
sortiert Fremd-Versionen (Remix, Sped Up, Slowed, Live, Instrumental, Cover-
Bands, Compilations) aus, damit nicht das Demo-/Sampler-Cover eingebettet wird.

Nur MP3 wird unterstuetzt (ID3 APIC-Frame). Fehler sind nicht fatal - der
Import gelingt auch ohne Cover.
"""

import logging
import re
import time

import httpx
from mutagen.id3 import ID3, APIC, error as ID3Error

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

_DEEZER_SEARCH_URL = "https://api.deezer.com/search"
_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"

# Zusaetze in Titel/Album, die auf eine unerwuenschte Fremd-Version hindeuten.
# Solche Treffer werden bei der Auswahl abgewertet/aussortiert.
_BAD_VERSION_MARKERS = (
    "remix", "sped up", "spedup", "slowed", "reverb", "nightcore",
    "instrumental", "karaoke", "piano", "guitar cover", "cover version",
    "live", "acoustic version", "tribute", "8d", "lofi", "demo",
    "rock version", "like a version", "made famous", "in the style of",
)
# Album-/Collection-Marker, die auf Sampler/Compilations hindeuten.
_COMPILATION_MARKERS = (
    "now that's what i call", "kidz bop", "hits ", "top 100", "top hits",
    "greatest hits of", "the sound of", "party mix", "bravo hits",
    "as mais tocadas", "compilation",
)


def _norm(s: str) -> str:
    """Normalisiert einen String fuer den Vergleich (klein, ohne Sonderzeichen)."""
    s = (s or "").lower().strip()
    # Klammer-Zusaetze wie (feat. ...), (Radio Edit) fuer den Kern-Vergleich weg.
    s = re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()

# Ab welcher Seiten-Abweichung ein Cover als "rechteckig" (korrekturbeduerftig)
# gilt. 0.15 = eine Seite darf bis 15% laenger sein und zaehlt noch als
# quadratisch. So bleiben minimal abweichende Album-Cover (z.B. 500x498)
# unangetastet, waehrend echte CD-Scans (Vorder+Rueckseite, ~2:1) erkannt
# werden. Konservativ, damit gute Cover nicht unnoetig ersetzt werden.
_SQUARE_TOLERANCE = 0.15


def _is_square(w: int, h: int) -> bool:
    """True, wenn Breite/Hoehe innerhalb der Toleranz als quadratisch gelten."""
    if w <= 0 or h <= 0:
        return True  # unbekannt -> nicht als fehlerhaft werten
    longer, shorter = max(w, h), min(w, h)
    return (longer - shorter) / longer <= _SQUARE_TOLERANCE


def _score_candidate(want_artist: str, want_title: str,
                     cand_artist: str, cand_title: str, cand_album: str,
                     is_single: bool) -> int | None:
    """
    Bewertet einen Suchtreffer. Hoeher = besser. None = disqualifiziert
    (falscher Artist/Titel oder klar unerwuenschte Version).
    """
    wa, wt = _norm(want_artist), _norm(want_title)
    ca, ct = _norm(cand_artist), _norm(cand_title)
    calbum = (cand_album or "").lower()
    ctitle_raw = (cand_title or "").lower()

    # Artist muss passen (einer im anderen enthalten - deckt "feat." ab).
    if wa and ca and wa not in ca and ca not in wa:
        return None
    # Titel-Kern muss passen.
    if wt and ct and wt != ct and wt not in ct and ct not in wt:
        return None
    # Unerwuenschte Fremd-Versionen (Remix/Sped Up/Piano/Cover ...) raus,
    # sofern der gesuchte Titel diese Marker nicht selbst enthaelt.
    for m in _BAD_VERSION_MARKERS:
        if m in ctitle_raw and m not in wt:
            return None

    score = 100
    if ct == wt:
        score += 30            # exakter Titel
    if is_single:
        score += 25            # dedizierte Single bevorzugen
    for m in _COMPILATION_MARKERS:
        if m in calbum:
            score -= 60        # Sampler/Compilation abwerten
            break
    if "demo" in calbum:
        score -= 40            # Demo-EP abwerten (z.B. Dominic Fike)
    if "deluxe" in calbum:
        score -= 5
    return score


def _itunes_search(artist: str, title: str):
    """
    Ruft die iTunes-Search-API mit Retry bei Drosselung (HTTP 429/403) auf.
    Wartet zwischen den Versuchen exponentiell (ITUNES_RETRY_BASE_MS, dann x2).
    Gibt das JSON-Payload zurueck oder None (endgueltig fehlgeschlagen/gedrosselt).
    """
    params = {"term": f"{artist} {title}", "entity": "song", "limit": 15}
    attempts = max(1, config.ITUNES_MAX_RETRIES)
    with httpx.Client(timeout=15.0, follow_redirects=True,
                      headers={"User-Agent": "charts-import/1.0"}) as client:
        for attempt in range(attempts):
            try:
                resp = client.get(_ITUNES_SEARCH_URL, params=params)
            except httpx.HTTPError as e:
                logger.debug(t("import.cover.itunes_network_error", artist=artist,
                               title=title, error=e))
                return None
            # Drosselung -> warten und erneut versuchen (ausser beim letzten Mal).
            if resp.status_code in (429, 403):
                if attempt < attempts - 1:
                    wait = config.ITUNES_RETRY_BASE_MS * (2 ** attempt) / 1000.0
                    logger.debug(t("import.cover.itunes_throttled_retry",
                                   status=resp.status_code, wait=f"{wait:.1f}",
                                   artist=artist, title=title))
                    time.sleep(wait)
                    continue
                logger.debug(t("import.cover.itunes_throttled_fallback",
                               status=resp.status_code, artist=artist, title=title))
                return None
            try:
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.debug(t("import.cover.itunes_search_failed", artist=artist,
                               title=title, error=e))
                return None
    return None


def _find_cover_url_itunes(artist: str, title: str) -> str | None:
    """Sucht das beste Single-/Album-Cover bei iTunes/Apple (mit Retry)."""
    payload = _itunes_search(artist, title)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not results:
        return None

    best_url, best_score = None, -1
    for r in results:
        coll = r.get("collectionName") or ""
        is_single = coll.lower().endswith("- single") or " - single" in coll.lower()
        score = _score_candidate(
            artist, title,
            r.get("artistName", ""), r.get("trackName", ""), coll, is_single,
        )
        if score is None or score <= best_score:
            continue
        art = r.get("artworkUrl100") or r.get("artworkUrl60") or ""
        if not art:
            continue
        # Apple liefert die Groesse ueber den Dateinamen; hochskalieren.
        size = config.ITUNES_COVER_SIZE
        art = re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.\\1", art)
        best_url, best_score = art, score

    return best_url


def _find_cover_url_deezer(artist: str, title: str) -> str | None:
    """Sucht das beste Album-Cover bei Deezer (Fallback)."""
    for query in (f'artist:"{artist}" track:"{title}"', f"{artist} {title}"):
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True,
                              headers={"User-Agent": "charts-import/1.0"}) as client:
                resp = client.get(_DEEZER_SEARCH_URL, params={"q": query, "limit": 10})
                resp.raise_for_status()
                payload = resp.json()
        except Exception as e:
            logger.debug(t("import.cover.deezer_search_failed", artist=artist,
                           title=title, error=e))
            continue

        data = payload.get("data") if isinstance(payload, dict) else None
        if not data:
            continue

        best_url, best_score = None, -1
        for tr in data:
            album = (tr or {}).get("album") or {}
            artist_obj = (tr or {}).get("artist") or {}
            score = _score_candidate(
                artist, title,
                artist_obj.get("name", ""), tr.get("title", ""),
                album.get("title", ""), is_single=False,
            )
            if score is None or score <= best_score:
                continue
            url = (album.get("cover_xl") or album.get("cover_big")
                   or album.get("cover_medium"))
            if url:
                best_url, best_score = url, score
        if best_url:
            return best_url
    return None


def _find_cover_url(artist: str, title: str) -> str | None:
    """
    Sucht die beste Cover-URL ueber die konfigurierten Quellen (COVER_SOURCES),
    in Reihenfolge. Erste Quelle mit Treffer gewinnt.
    """
    for source in config.COVER_SOURCES:
        if source == "itunes":
            url = _find_cover_url_itunes(artist, title)
        elif source == "deezer":
            url = _find_cover_url_deezer(artist, title)
        else:
            continue
        if url:
            logger.debug(t("import.cover.source_hit", source=source, artist=artist,
                           title=title, url=url))
            return url
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """
    Liest Breite/Hoehe eines JPEG aus den Segment-Headern, OHNE Bibliothek.
    Gibt (width, height) oder None (kein/unlesbares JPEG).
    """
    if not data or data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # SOF0..SOF15 (ausser DHT=C4, DAC=CC, RSTn) enthalten die Dimensionen.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 <= n:
                height = (data[i + 5] << 8) | data[i + 6]
                width = (data[i + 7] << 8) | data[i + 8]
                return width, height
            return None
        # Segmentlaenge ueberspringen.
        if i + 3 >= n:
            break
        seg_len = (data[i + 2] << 8) | data[i + 3]
        if seg_len <= 0:
            break
        i += 2 + seg_len
    return None


def _make_square(data: bytes) -> bytes:
    """
    Sorgt dafuer, dass das Cover quadratisch ist.

    - Ist Pillow verfuegbar: mittig quadratisch zuschneiden (kleinere Kante),
      als JPEG zurueckgeben. Funktioniert fuer alle Bildformate.
    - Ohne Pillow: bereits quadratische JPEGs unveraendert zurueckgeben;
      rechteckige werden abgelehnt (None), damit kein verzerrtes Cover
      eingebettet wird.

    Rueckgabe: quadratische Bilddaten (bytes) oder None (rechteckig + kein
    Pillow -> nicht einbetten).
    """
    dims = _jpeg_dimensions(data)
    # Schon (nahezu) quadratisch? Direkt akzeptieren.
    if dims and _is_square(dims[0], dims[1]):
        return data

    try:
        import io
        from PIL import Image
    except Exception:
        # Kein Pillow: rechteckiges Cover ablehnen, quadratisches (dims=None
        # unklar) vorsichtshalber durchlassen.
        if dims is None:
            return data  # unbekannt -> nicht blockieren
        logger.info(t("import.cover.nonsquare_no_pillow", width=dims[0], height=dims[1]))
        return None

    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        if _is_square(w, h):
            return data
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        logger.info(t("import.cover.cropped_square", w=w, h=h, side=side))
        return out.getvalue()
    except Exception as e:
        logger.debug(t("import.cover.square_failed", error=e))
        return data  # im Zweifel Original einbetten


def _download(url: str) -> bytes | None:
    """Laedt das Cover-Bild herunter (JPEG)."""
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True,
                          headers={"User-Agent": "charts-import/1.0"}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        logger.debug(t("import.cover.download_failed", url=url, error=e))
        return None


def _has_embedded_cover(path: str) -> bool:
    """True, wenn die MP3 bereits ein eingebettetes Bild (APIC) hat."""
    try:
        tags = ID3(path)
        return any(k.startswith("APIC") for k in tags.keys())
    except ID3Error:
        return False
    except Exception:
        return False


def has_cover(path: str) -> bool:
    """Oeffentlich: True, wenn die MP3 bereits ein eingebettetes Cover hat."""
    return _has_embedded_cover(path)


def _embedded_cover_bytes(path: str) -> bytes | None:
    """Liefert die Rohdaten des ersten eingebetteten Covers (APIC) oder None."""
    try:
        tags = ID3(path)
    except Exception:
        return None
    for k in tags.keys():
        if k.startswith("APIC"):
            try:
                return tags[k].data
            except Exception:
                return None
    return None


def cover_status(path: str) -> str:
    """
    Klassifiziert das eingebettete Cover einer MP3:
      "none"      - kein Cover
      "square"    - quadratisches Cover (in Ordnung)
      "nonsquare" - rechteckiges Cover (z.B. CD-Scan Vorder+Rueckseite) -> fehlerhaft
      "unknown"   - Cover vorhanden, Dimensionen nicht lesbar
    """
    data = _embedded_cover_bytes(path)
    if not data:
        return "none"
    dims = _jpeg_dimensions(data)
    if dims is None:
        # Kein JPEG (evtl. PNG) - Dimensionen ueber Pillow versuchen.
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                dims = im.size
        except Exception:
            return "unknown"
    w, h = dims
    return "square" if _is_square(w, h) else "nonsquare"


def fetch_and_embed(path: str, artist: str, title: str) -> bool:
    """
    Sucht das Deezer-Cover fuer artist/title und bettet es in die MP3 ein.
    Prueft NICHT das EMBED_COVERS-Flag und NICHT, ob schon ein Cover da ist -
    das entscheidet der Aufrufer. Rueckgabe: True bei Erfolg.
    """
    if not path.lower().endswith(".mp3"):
        return False

    url = _find_cover_url(artist, title)
    if not url:
        logger.debug(t("import.cover.none_found", artist=artist, title=title))
        return False

    data = _download(url)
    if not data:
        return False

    # Sicherstellen, dass das Cover quadratisch ist (rechteckige sehen beim
    # Abspielen verzerrt aus). Bei rechteckig + fehlendem Pillow: nicht einbetten.
    data = _make_square(data)
    if not data:
        return False

    try:
        try:
            tags = ID3(path)
        except ID3Error:
            tags = ID3()  # noch keine ID3-Tags vorhanden
        tags.delall("APIC")
        tags.add(APIC(
            encoding=3,          # UTF-8
            mime="image/jpeg",
            type=3,              # Cover (front)
            desc="Cover",
            data=data,
        ))
        tags.save(path)
        return True
    except Exception as e:
        logger.warning(t("import.cover.embed_failed", path=path, error=e))
        return False


def embed_cover(path: str, artist: str, title: str, overwrite: bool = False) -> bool:
    """
    Sucht + bettet ein Deezer-Cover in die MP3 ein (fuer den Import).
    Beruecksichtigt das EMBED_COVERS-Flag.

    overwrite=False: Dateien, die schon ein Cover haben, werden uebersprungen.
    overwrite=True:  Ein vorhandenes Cover wird ERSETZT. Das ist wichtig fuer
        Chart-Songs, die aus einer Compilation stammen - deren eingebettetes
        Bild ist das Sampler-Cover, nicht das der Single. Das Deezer-Cover wird
        anhand von artist/title gesucht und liefert das ORIGINAL-Album-/Single-
        Cover. Wird nur ersetzt, wenn Deezer tatsaechlich ein Cover liefert
        (sonst bleibt das vorhandene erhalten - kein Cover-Verlust).

    Rueckgabe: True, wenn ein Cover eingebettet/ersetzt wurde.
    """
    if not config.EMBED_COVERS:
        return False
    if not path.lower().endswith(".mp3"):
        return False
    if _has_embedded_cover(path) and not overwrite:
        return False
    # Bei overwrite: fetch_and_embed setzt nur, wenn Deezer ein Cover liefert
    # (delall("APIC") passiert erst NACH erfolgreichem Download). Kein Risiko,
    # ein vorhandenes Cover ersatzlos zu loeschen.
    return fetch_and_embed(path, artist, title)
