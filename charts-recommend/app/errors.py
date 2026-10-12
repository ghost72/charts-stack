"""
Gemeinsame Fehlertypen fuer charts-recommend.
"""


class FetchError(Exception):
    """
    Transport-Fehler beim Abruf einer Empfehlungs-Quelle (Timeout, HTTP-Fehler,
    Verbindungsfehler) - NACHDEM alle Wiederholungen fehlgeschlagen sind.

    WICHTIG: Das ist bewusst getrennt von einem *leeren, aber erfolgreichen*
    Ergebnis (ListenBrainz hat z.B. noch keine Playlists generiert). Ein leeres
    Ergebnis ist KEIN Fehler, sondern fehlende Daten; nur ein FetchError
    signalisiert eine echte Stoerung (-> status="error" + Pushover-Alarm).
    """
