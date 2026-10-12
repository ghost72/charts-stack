"""
Doc tests for CHARTS-SOURCES.md (DE) + CHARTS-SOURCES.en.md (EN).

These tests are the firewall against docs that no longer match the real parser.
They extract the JSON examples of the "ready to run" section from BOTH doc
files and check:

  (a) each snippet is valid JSON (json.loads);
  (b) config._parse_charts accepts it WITHOUT validation WARN/error (caplog);
  (c) a legacy-format example WITHOUT 'service' yields the former feed mapping
      (type:chart->deezer/chart, type:playlist->deezer/playlist,
      type:lastfm->lastfm);
  (d) the runnable snippets quoted in DE and EN are BYTE-identical.

The single source of truth are the 'runnable' entries from
tests/fixtures/charts_examples.json (NIT-5): the docs quote exactly these
snippets, and the test compares the blocks extracted from the docs against them.
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

# A runnable example in the docs is marked with an HTML comment marker directly
# before the compact (single-line) JSON code block so the test finds the "ready
# to run" snippets unambiguously (and ignores placeholder/read-only blocks):
#   <!-- runnable:minimal -->
#   ```json
#   [ ... one line ... ]
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
    """Read all JSON blocks of a file marked with <!-- runnable:KEY -->."""
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


# --- (a)+(b) Each 'ready to run' snippet is valid & parser-conforming --------

def test_de_runnable_blocks_present():
    blocks = _extract_runnable(_DOC_DE)
    assert blocks, "CHARTS-SOURCES.md enthaelt keine <!-- runnable:* --> Bloecke."


@pytest.mark.parametrize("doc_path", [_DOC_DE, _DOC_EN], ids=["de", "en"])
def test_runnable_snippets_valid_and_parserconform(doc_path, fixture, monkeypatch, caplog):
    blocks = _extract_runnable(doc_path)
    runnable = fixture["runnable"]
    # Every fixture 'runnable' entry must be quoted in the docs.
    assert set(blocks) == set(runnable), (
        f"{doc_path.name}: runnable-Marker {sorted(blocks)} != "
        f"Fixture {sorted(runnable)}")

    for key, body in blocks.items():
        # (a) valid JSON
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            pytest.fail(f"{doc_path.name} runnable:{key} ist kein gueltiges JSON: {e}")

        # Matches the fixture (single source of truth).
        assert parsed == runnable[key], (
            f"{doc_path.name} runnable:{key} weicht von der Fixture ab.")

        # (b) parser-conforming WITHOUT WARN/error
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            sources = _parse(monkeypatch, body)
        warn_err = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warn_err, (
            f"{doc_path.name} runnable:{key} loest Validierungs-Warnungen aus: "
            f"{[r.message for r in warn_err]}")
        # A runnable example yields at least one playlist.
        assert sources, f"{doc_path.name} runnable:{key} ergab keine Playlist."


# --- (c) Legacy format without 'service' == former mapping ------------------

def test_legacy_without_service_backward_compatible(fixture, monkeypatch, caplog):
    data = fixture["legacy_no_service"]["mixed"]
    with caplog.at_level(logging.WARNING):
        sources = _parse(monkeypatch, json.dumps(data))
    # No validation warnings for the (valid) legacy config.
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


# --- (d) DE and EN quote byte-identical runnable snippets -------------------

def test_de_en_runnable_snippets_identical():
    de = _extract_runnable(_DOC_DE)
    en = _extract_runnable(_DOC_EN)
    assert set(de) == set(en), (
        f"runnable-Marker weichen ab: DE={sorted(de)} EN={sorted(en)}")
    for key in de:
        assert de[key] == en[key], (
            f"runnable:{key} unterscheidet sich zwischen DE und EN "
            f"(muss byte-identisch sein).")
