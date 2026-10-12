"""
Normalisierung und Aehnlichkeitsvergleich fuer das Matching von
slskd-Suchergebnissen gegen "Artist - Titel".

Die Logik ist bewusst identisch zu charts-sync (app/library.py), damit beide
Dienste Songs auf dieselbe, tolerante Art vergleichen.
"""

import re
import unicodedata
from difflib import SequenceMatcher

# Zusaetze, die vor dem Vergleich aus Titeln entfernt werden.
_NOISE_PATTERNS = [
    r"\(feat\.?.*?\)", r"\[feat\.?.*?\]",
    r"\(ft\.?.*?\)", r"\[ft\.?.*?\]",
    r"\(with.*?\)",
    r"\(remaster.*?\)", r"\[remaster.*?\]",
    r"\(.*?version\)", r"\(.*?edit\)",
    r"\(official.*?\)", r"\(lyric.*?\)", r"\(audio\)", r"\(video\)",
]


def normalize(text: str) -> str:
    """Normalisiert einen String fuer den Vergleich (siehe charts-sync)."""
    if not text:
        return ""
    text = text.lower()
    for pattern in _NOISE_PATTERNS:
        text = re.sub(pattern, " ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> int:
    """Aehnlichkeit zweier normalisierter Strings als Wert 0-100."""
    if not a or not b:
        return 0
    return round(SequenceMatcher(None, a, b).ratio() * 100)


# Marker im Pfad, die auf eine Compilation / Sampler / Chart-Zusammenstellung
# hindeuten (statt Single oder regulaeres Album). slskd liefert nur Pfade,
# keine echten Metadaten - das ist daher eine Heuristik, kein sicheres Signal.
_COMPILATION_MARKERS = [
    "va -", "va-", "various", "verschiedene",
    "varios", "varias",                  # span./ital. "Various"
    "compilation", "sampler", "mastermix", "megamix", "mixtape",
    "top 100", "top100", "top 40", "top40", "charts", "hitlist", "hit list",
    "hits", "greatest hits", "best of", "best ever", "the essential",
    "nouveautes", "nouveaute",           # normalisiert ohne Akzent
    "new music friday", "new music",
    "muziek singles", "singles flac", "singles pack",
    "week ", "semaine", "vol ", "volume ", "disc ", "cd1", "cd 1", "cd2", "cd 2",
    "now that", "bravo hits", "kontor", "clubland", "dance max", "maxi power",
    "party hits", "decades", "collection", "anthology",
]


def is_compilation(path: str) -> bool:
    """
    Heuristik: True, wenn der Datei-/Ordnerpfad auf eine Compilation/Sampler
    hindeutet. Prueft den GESAMTEN Pfad (nicht nur den Dateinamen), da der
    Compilation-Hinweis meist im Ordnernamen steht.
    """
    norm = normalize(path.replace("\\", "/"))
    return any(marker in norm for marker in _COMPILATION_MARKERS)


# Schluesselwoerter, die auf eine unerwuenschte Version hindeuten: YouTube-Rips
# ("lyrics", "audio", "video"), Bootlegs/Edits (acapella, remix, mashup),
# Live-/Karaoke-/Cover-Versionen usw. Solche Dateien will Lidarr/Navidrome
# nicht - sie werden komplett verworfen, egal wie gut der Titel matcht.
# Wird gegen den normalisierten Dateinamen geprueft (normalize entfernt Klammern,
# daher stehen die Marker hier ohne Klammern).
DEFAULT_EXCLUDE_KEYWORDS = [
    # YouTube-/Rip-Marker
    "lyrics", "lyric video", "official video", "official audio", "music video",
    "visualizer", "video edit",
    # Bearbeitungen / Bootlegs
    "acapella", "a capella", "instrumental", "karaoke",
    "remix", "mashup", "bootleg", "flip", "rework", "vip mix",
    "extended mix", "club mix", "radio mix", "dance mix",
    # abweichende Auffuehrungen
    "live", "cover", "tribute", "unplugged",
    # Trend-Versionen
    "sped up", "spedup", "slowed", "reverb", "nightcore", "8d", "8d audio",
    # Fragmente
    "snippet", "teaser", "preview", "loop",
    # dein konkreter Fall (DJ-Edits / Acapella-Break-Ins)
    "break in",
    # DJ-Pool-/Serato-Edits (kommen v.a. beim Titel-only-Fallback hoch). Bewusst
    # als Mehr-Wort-Marker, damit einzelne legitime Titelwoerter (z.B. "Dirty
    # Diana", "Clean Bandit") NICHT faelschlich verworfen werden.
    "intro clean", "clean intro", "intro dirty", "dirty intro",
    "uptempo", "downtempo", "transition", "acap out", "acapella out",
    "quick hit", "short edit", "redrum", "hyper remix", "dj edit", "dj intro",
    # DJ-Pool-/Edit-Anbieter-Marker (kommen aus Sammel-Rips, sind keine
    # Originalversionen). "froogle" z.B. bei "DtMF (Froogle Edit)".
    "froogle", "starjunk", "djcity", "mastermix", "ultimix", "funkymix",
    "hypeedit", "bpm supreme", "digital djpool", "clean edit", "dirty edit",
]


def looks_like_dj_edit(filename: str) -> bool:
    """
    Heuristik fuer DJ-Pool-Edits: Dateiname endet auf eine BPM-Zahl (70-199),
    z.B. "... Die With A Smile (Uptempo Edit) 124.mp3". Solche Dateien sind
    fast immer Edits/Transitions, keine Originalversionen. Wir wollen nur
    Originale, daher verwerfen.

    Bewusst eng: nur eine Zahl UNMITTELBAR am Ende des Basisnamens (ohne
    Endung). So werden Jahreszahlen in Klammern (z.B. "(2024)") oder Track-
    nummern am Anfang nicht getroffen.
    """
    import os

    base = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0].strip()
    m = re.search(r"(\d{2,3})$", base)
    if not m:
        return False
    n = int(m.group(1))
    # Typischer BPM-Bereich. Jahreszahlen (19xx/20xx) sind 4-stellig -> nicht
    # betroffen. Zahlen wie "124", "128", "140" schon.
    return 70 <= n <= 199


def has_excluded_keyword(filename: str, keywords: list[str]) -> str | None:
    """
    Prueft, ob im Dateinamen ein Ausschluss-Keyword vorkommt.
    Gibt das gefundene Keyword zurueck (fuer Logging) oder None.

    Wichtig: Es wird der ROHE Dateiname (kleingeschrieben) geprueft, NICHT der
    per normalize() bereinigte - denn normalize entfernt genau diese Marker
    (z.B. "(Lyrics)") absichtlich fuers Matching. Hier wollen wir sie aber sehen.
    """
    import os

    base = os.path.basename(filename.replace("\\", "/")).lower()
    # Klammern/Zeichen zu Leerzeichen, damit "(lyrics)" als "lyrics" gefunden wird.
    cleaned = re.sub(r"[^a-z0-9]+", " ", base)
    cleaned = f" {cleaned} "  # Wortgrenzen-Schutz
    for kw in keywords:
        kw_norm = f" {kw.lower().strip()} "
        if kw_norm in cleaned:
            return kw.strip()
    return None


def is_excluded_artist(artist: str, excluded: list[str]) -> bool:
    """
    True, wenn 'artist' auf einen global ausgeschlossenen Kuenstler passt
    (config.EXCLUDE_ARTISTS). Vergleich ueber normalisierte Namen, damit
    Gross-/Kleinschreibung, Akzente und Sonderzeichen egal sind.

    Trifft auch feat.-Kombinationen: Der ausgeschlossene Name muss als
    zusammenhaengende Wortfolge im normalisierten Artist-String vorkommen. So
    matcht "Trauffer" auch bei "Trauffer feat. X" oder "X & Trauffer" - aber der
    Kern-Namensvergleich verhindert, dass ein Teilwort faelschlich zuschlaegt.
    """
    if not excluded:
        return False
    a = normalize(artist)
    if not a:
        return False
    a_padded = f" {a} "
    for name in excluded:
        n = normalize(name)
        if n and f" {n} " in a_padded:
            return True
    return False


def artist_in_path(artist: str, filename: str) -> bool:
    """
    Prueft, ob der Kuenstlername im Datei-PFAD (Ordner + Dateiname) vorkommt.
    Wird fuer die Absicherung der Titel-only-Fallback-Suche genutzt: Da bei
    reiner Titelsuche auch Coverversionen anderer Kuenstler zurueckkommen
    koennen, akzeptieren wir einen Treffer nur, wenn der Kuenstler irgendwo im
    Pfad steht.

    Tolerant: prueft, ob (fast) alle Wortbestandteile des normalisierten
    Kuenstlernamens im normalisierten Pfad vorkommen. So matcht "Lady Gaga"
    auch in ".../Lady Gaga - Die With A Smile.mp3" oder
    ".../Gaga/Die With A Smile.mp3" - aber nicht in einem Fremd-Kuenstler-Pfad.
    """
    a = normalize(artist)
    if not a:
        return False
    path_norm = normalize(filename.replace("\\", "/"))
    words = [w for w in a.split() if len(w) > 1]
    if not words:
        # Einbuchstabige/kurze Kuenstler: ganzer String muss vorkommen.
        return a in path_norm
    present = sum(1 for w in words if w in path_norm)
    # Mindestens die Haelfte der Kuenstler-Woerter (aufgerundet) muss im Pfad
    # sein - toleriert Feature-Zusaetze, verlangt aber den Kern des Namens.
    return present >= (len(words) + 1) // 2


def match_score(query_norm: str, filename: str) -> int:
    """
    Score 0-100, wie gut ein slskd-Dateiname zur normalisierten Suchanfrage
    ("artist title") passt.

    Da slskd-Ergebnisse nur Dateipfade liefern (keine sauberen Tags), wird der
    Basisname der Datei normalisiert und gegen die Anfrage verglichen. Zusaetz-
    lich wird geprueft, ob alle Wortbestandteile der Anfrage im Dateinamen
    vorkommen (schuetzt vor rein zufaelliger SequenceMatcher-Aehnlichkeit).
    """
    import os

    base = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0]
    base_norm = normalize(base)
    if not base_norm:
        return 0

    seq = similarity(query_norm, base_norm)

    # Wort-Abdeckung: Anteil der Anfrage-Woerter, die im Dateinamen stehen.
    q_words = [w for w in query_norm.split() if len(w) > 1]
    if q_words:
        present = sum(1 for w in q_words if w in base_norm)
        coverage = round(present / len(q_words) * 100)
    else:
        coverage = 0

    # Kombiniert: Wort-Abdeckung ist das staerkere Signal fuer Dateinamen.
    return round(coverage * 0.6 + seq * 0.4)
