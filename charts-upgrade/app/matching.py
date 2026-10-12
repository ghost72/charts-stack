"""
Normalisierung, Aehnlichkeitsvergleich und Compilation-Heuristik fuer das
Matching von slskd-Suchergebnissen gegen "Artist - Titel".

KOPIE aus charts-download/charts-songrequest (bewusst dupliziert, keine
Laufzeit-Abhaengigkeit zwischen den Diensten - wie bei den anderen charts-*
Modulen). Aenderungen hier bei Bedarf mit den Schwester-Diensten abgleichen.
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
    "party hits", "decades", "collection", "anthology", "kuschelrock",
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
    # DJ-Edits / Acapella-Break-Ins
    "break in",
    "intro clean", "clean intro", "intro dirty", "dirty intro",
    "uptempo", "downtempo", "transition", "acap out", "acapella out",
    "quick hit", "short edit", "redrum", "hyper remix", "dj edit", "dj intro",
    # DJ-Pool-/Edit-Anbieter-Marker
    "froogle", "starjunk", "djcity", "mastermix", "ultimix", "funkymix",
    "hypeedit", "bpm supreme", "digital djpool", "clean edit", "dirty edit",
]


def looks_like_dj_edit(filename: str) -> bool:
    """
    Heuristik fuer DJ-Pool-Edits: Dateiname endet auf eine BPM-Zahl (70-199).
    Solche Dateien sind fast immer Edits/Transitions, keine Originalversionen.
    """
    import os

    base = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0].strip()
    m = re.search(r"(\d{2,3})$", base)
    if not m:
        return False
    n = int(m.group(1))
    return 70 <= n <= 199


def has_excluded_keyword(filename: str, keywords: list[str]) -> str | None:
    """
    Prueft, ob im Dateinamen ein Ausschluss-Keyword vorkommt.
    Gibt das gefundene Keyword zurueck (fuer Logging) oder None.
    """
    import os

    base = os.path.basename(filename.replace("\\", "/")).lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", base)
    cleaned = f" {cleaned} "  # Wortgrenzen-Schutz
    for kw in keywords:
        kw_norm = f" {kw.lower().strip()} "
        if kw_norm in cleaned:
            return kw.strip()
    return None


def is_excluded_artist(artist: str, excluded: list[str]) -> bool:
    """
    True, wenn 'artist' auf einen global ausgeschlossenen Kuenstler passt.
    Vergleich ueber normalisierte Namen; trifft auch feat.-Kombinationen.
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
    Fuer die Absicherung der Titel-only-Fallback-Suche (Coverschutz).
    """
    a = normalize(artist)
    if not a:
        return False
    path_norm = normalize(filename.replace("\\", "/"))
    words = [w for w in a.split() if len(w) > 1]
    if not words:
        return a in path_norm
    present = sum(1 for w in words if w in path_norm)
    return present >= (len(words) + 1) // 2


def match_score(query_norm: str, filename: str) -> int:
    """
    Score 0-100, wie gut ein slskd-Dateiname zur normalisierten Suchanfrage
    ("artist title") passt.
    """
    import os

    base = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0]
    base_norm = normalize(base)
    if not base_norm:
        return 0

    seq = similarity(query_norm, base_norm)

    q_words = [w for w in query_norm.split() if len(w) > 1]
    if q_words:
        present = sum(1 for w in q_words if w in base_norm)
        coverage = round(present / len(q_words) * 100)
    else:
        coverage = 0

    return round(coverage * 0.6 + seq * 0.4)
