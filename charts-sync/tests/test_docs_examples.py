"""
Doku-Tests fuer CHARTS-SOURCES.md (DE) + CHARTS-SOURCES.en.md (EN).

Diese Tests sind die Brandmauer gegen eine Doku, die nicht mehr zum echten
Parser passt. Sie extrahieren die JSON-Beispiele der Rubrik "sofort lauffaehig"
aus BEIDEN Doku-Dateien und pruefen:

  (a) jedes Snippet ist gueltiges JSON (json.loads);
  (b) config._parse_charts akzeptiert es OHNE Validierungs-WARN/Error (caplog);
  (c) ein Alt-Format-Beispiel OHNE 'service' ergibt die bisherige Feed-
      Zuordnung (type:chart->deezer/chart, type:playlist->deezer/playlist,
      type:lastfm->lastfm);
  (d) die in DE und EN zitierten lauffaehigen Snippets sind BYTE-identisch.

Single Source of Truth sind die 'runnable'-Eintraege aus
tests/fixtures/charts_examples.json (NIT-5): die Doku zitiert exakt diese
Snippets, der Test vergleicht die aus der Doku extrahierten Bloecke dagegen.
"""

import json
import logging
import re
from pathlib import Path

import pytest

from app import config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC_DE = _REPO_ROOT / "CHARTS-SOURCES.md"
_DOC_EN = _REPO_ROOT / "CHARTS-SOURCES.en.md"
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "charts_examples.json"

# Ein lauffaehiges Beispiel in der Doku wird mit einem HTML-Kommentar-Marker
# direkt vor dem kompakten (einzeiligen) JSON-Codeblock ausgezeichnet, damit der
# Test die 'sofort lauffaehig'-Snippets eindeutig findet (und Platzhalter-/
# Lese-Bloecke ignoriert):
#   <!-- runnable:minimal -->
#   ```json
#   [ ... eine Zeile ... ]
#   ```
_RUNNABLE_BLOCK_RE = re.compile(
    r"<!--\s*runnable:(?P<key>[a-z0-9_]+)\s*-->\s*\n```json\n(?P<body>.*?)\n```",
    re.IGNORECASE | re.DOTALL,
)


@pytest.fixture(scope="module")
def fixture() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _extract_runnable(doc_path: Path) -> dict[str, str]:
    """Liest alle mit <!-- runnable:KEY --> markierten JSON-Bloecke einer Datei."""
    assert doc_path.exists(), f"Doku fehlt: {doc_path}"
    text = doc_path.read_text(encoding="utf-8")
    blocks: dict[str, str] = {}
    for m in _RUNNABLE_BLOCK_RE.finditer(text):
        key = m.group("key").lower()
        assert key not in blocks, f"Doppelter runnable-Marker '{key}' in {doc_path.name}"
        blocks[key] = m.group("body")
    return blocks


def _parse(monkeypatch, raw_json: str) -> list:
    monkeypatch.setenv("CHARTS", raw_json)
    return config._parse_charts()


# --- (a)+(b) Jedes 'sofort lauffaehig'-Snippet ist gueltig & parserkonform ---

def test_de_runnable_blocks_present():
    blocks = _extract_runnable(_DOC_DE)
    assert blocks, "CHARTS-SOURCES.md enthaelt keine <!-- runnable:* --> Bloecke."


@pytest.mark.parametrize("doc_path", [_DOC_DE, _DOC_EN], ids=["de", "en"])
def test_runnable_snippets_valid_and_parserconform(doc_path, fixture, monkeypatch, caplog):
    blocks = _extract_runnable(doc_path)
    runnable = fixture["runnable"]
    # Jeder Fixture-'runnable'-Eintrag muss in der Doku zitiert sein.
    assert set(blocks) == set(runnable), (
        f"{doc_path.name}: runnable-Marker {sorted(blocks)} != "
        f"Fixture {sorted(runnable)}")

    for key, body in blocks.items():
        # (a) gueltiges JSON
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            pytest.fail(f"{doc_path.name} runnable:{key} ist kein gueltiges JSON: {e}")

        # Deckt sich mit der Fixture (Single Source of Truth).
        assert parsed == runnable[key], (
            f"{doc_path.name} runnable:{key} weicht von der Fixture ab.")

        # (b) parserkonform OHNE WARN/Error
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            sources = _parse(monkeypatch, body)
        warn_err = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warn_err, (
            f"{doc_path.name} runnable:{key} loest Validierungs-Warnungen aus: "
            f"{[r.message for r in warn_err]}")
        # Ein lauffaehiges Beispiel ergibt mindestens eine Playlist.
        assert sources, f"{doc_path.name} runnable:{key} ergab keine Playlist."


# --- (c) Alt-Format ohne 'service' == bisherige Zuordnung -------------------

def test_legacy_without_service_backward_compatible(fixture, monkeypatch, caplog):
    data = fixture["legacy_no_service"]["mixed"]
    with caplog.at_level(logging.WARNING):
        sources = _parse(monkeypatch, json.dumps(data))
    # Keine Validierungs-Warnungen fuer die (gueltige) Alt-Config.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    assert len(sources) == 1
    feeds = sources[0].feeds
    assert len(feeds) == 3

    # type:"chart" -> Deezer/chart
    assert feeds[0].kind == "deezer"
    assert feeds[0].deezer_type == "chart"
    assert feeds[0].source == "0"

    # type:"playlist" -> Deezer/playlist
    assert feeds[1].kind == "deezer"
    assert feeds[1].deezer_type == "playlist"
    assert feeds[1].source == "1313617925"

    # type:"lastfm" -> Last.fm (method lowercased, country in params)
    assert feeds[2].kind == "lastfm"
    assert feeds[2].method == "geo.gettoptracks"
    assert feeds[2].params == {"country": "Switzerland"}


# --- (d) DE und EN zitieren byte-identische lauffaehige Snippets ------------

def test_de_en_runnable_snippets_identical():
    de = _extract_runnable(_DOC_DE)
    en = _extract_runnable(_DOC_EN)
    assert set(de) == set(en), (
        f"runnable-Marker weichen ab: DE={sorted(de)} EN={sorted(en)}")
    for key in de:
        assert de[key] == en[key], (
            f"runnable:{key} unterscheidet sich zwischen DE und EN "
            f"(muss byte-identisch sein).")
