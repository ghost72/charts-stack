"""
Repo-root parity guard for the stack's duplicated helper logic.

The charts-stack deliberately DUPLICATES a handful of pure helper functions into
every service (copy-convention: each container is independently deployable and
never imports from a sibling at runtime). The cost of that architecture is
DRIFT - when one service gets a fix, the sibling copies silently diverge and the
divergence resurfaces later as a production symptom. This test pins the
duplicated logic to one behaviour so any future drift fails here at edit time,
analogous to the existing per-service i18n key-parity test (test_i18n.py).

It is self-contained and pure-Python: `pip install pytest && pytest` with no
network, no Docker, no ffmpeg, no audio decode, and no real mutagen/httpx
install. Each service's helper module is loaded by file path under a unique
synthetic module name (all services name their package `app`, so a plain
`from app import ...` cannot address more than one at a time). Before any load
we register lightweight `sys.modules` stubs so the modules - which pull heavier
third-party deps at module level - exec cleanly without those packages:

  - the five library.py copies (and charts-config/app/tags.py) do
    `from mutagen import File` at module top;
  - charts-import/app/importer.py is the heavy case: its first-line
    `from . import config, cover, quality, replaygain, resolve, tags, yearmap,
    mbyear` transitively pulls `import httpx` (cover.py/mbyear.py) and
    `from mutagen.id3 import ID3, APIC, TXXX, error, ID3NoHeaderError`
    (cover.py/replaygain.py).

None of the helpers UNDER TEST ever call these runtime objects, so name-only
stubs suffice to satisfy import-time resolution.
"""

import importlib.util
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent  # charts-stack/


# --- dependency stubs (registered once, before any module load) -------------
# Driven by the module-level third-party imports listed in the docstring above.
if "mutagen" not in sys.modules:
    _mutagen_stub = types.ModuleType("mutagen")
    _mutagen_stub.File = lambda *a, **k: None  # only the NAME is needed at import
    sys.modules["mutagen"] = _mutagen_stub
    _mid3 = types.ModuleType("mutagen.id3")
    for _n in ("ID3", "APIC", "TXXX"):
        setattr(_mid3, _n, type(_n, (object,), {}))
    _mid3.error = type("error", (Exception,), {})
    _mid3.ID3NoHeaderError = type("ID3NoHeaderError", (Exception,), {})
    sys.modules["mutagen.id3"] = _mid3
if "httpx" not in sys.modules:
    sys.modules["httpx"] = types.ModuleType("httpx")


_LOADED: dict[str, types.ModuleType] = {}


def _load(service: str, rel: str) -> types.ModuleType:
    """
    Load ``<service>/app/<rel>`` by path under a unique synthetic module name.

    Each service gets its own parent-package shim so the module's relative
    imports (`from . import config, tags`, `from .i18n import t`, ...) resolve
    against that service's real ``app/`` directory without colliding with any
    other service's ``app`` package.
    """
    key = f"{service}/{rel}"
    if key in _LOADED:
        return _LOADED[key]
    pkg = f"_parity_pkg_{service}"
    if pkg not in sys.modules:
        shim = types.ModuleType(pkg)
        shim.__path__ = [str(ROOT / service / "app")]
        sys.modules[pkg] = shim
    name = f"{pkg}.{rel.replace('/', '_').replace('.py', '')}"
    path = ROOT / service / "app" / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    _LOADED[key] = mod
    return mod


# --- FR-1a: byte-identity of every presence.py copy -------------------------

# Every service that carries a presence.py copy. The reference is charts-sync.
# A path that does not exist yet (before P1 adds the three new copies) is
# skipped, so the assertion is green on the pre-P1 tree and tightens
# automatically once flashback/recommend/hitparade gain their copy.
_PRESENCE_SERVICES = [
    "charts-sync", "charts-energy",
    "charts-flashback", "charts-recommend", "charts-hitparade",
]
_PRESENCE_REFERENCE = "charts-sync"


def test_presence_byte_identity():
    ref_path = ROOT / _PRESENCE_REFERENCE / "app" / "presence.py"
    reference = ref_path.read_bytes()
    checked = 0
    for service in _PRESENCE_SERVICES:
        path = ROOT / service / "app" / "presence.py"
        if not path.exists():
            continue  # copy not created yet (pre-P1) - tightens once it lands
        checked += 1
        assert path.read_bytes() == reference, (
            f"{service}/app/presence.py is not byte-identical to "
            f"{_PRESENCE_REFERENCE}/app/presence.py (copy-drift)"
        )
    # The reference itself must have been among the checked copies.
    assert checked >= 1


# --- FR-1b: _title_key behavioural parity -----------------------------------

_TITLE_KEY_SERVICES = {
    "charts-songrequest": ("library.py",),
    "charts-energy": ("presence.py",),
    "charts-sync": ("presence.py",),
    "charts-import": ("importer.py",),
    "charts-config": ("library.py",),
}

_TITLE_CORPUS = [
    "Inside My Soul (320 Kbps)",
    "Song (Extended Mix)",
    "Song - 320k",
    "Ewigi Liäbi",
    "Song (Part 2)",
    "Beyoncé — Déjà Vu",
]


def _title_key_funcs():
    out = {}
    for service, (rel,) in _TITLE_KEY_SERVICES.items():
        mod = _load(service, rel)
        out[service] = mod._title_key
    return out


def test_title_key_parity():
    funcs = _title_key_funcs()
    for title in _TITLE_CORPUS:
        keys = {svc: fn(title) for svc, fn in funcs.items()}
        distinct = set(keys.values())
        assert len(distinct) == 1, (
            f"_title_key disagrees on {title!r}: {keys}"
        )
    # "Song (Part 2)" must stay DISTINCT from "Song" in every copy.
    for svc, fn in funcs.items():
        assert fn("Song (Part 2)") != fn("Song"), (
            f"{svc}: _title_key folded 'Song (Part 2)' onto 'Song'"
        )


# --- FR-1c: _artist_key behavioural parity ----------------------------------

_ARTIST_KEY_SERVICES = {
    "charts-songrequest": ("library.py",),
    "charts-energy": ("presence.py",),
    "charts-sync": ("presence.py",),
    "charts-config": ("library.py",),
}

_ARTIST_CORPUS = ["Motörhead", "Beyoncé", "Sigur Rós", "DJ.ILHAM", "P!nk"]


def test_artist_key_parity():
    funcs = {
        svc: _load(svc, rel)._artist_key
        for svc, (rel,) in _ARTIST_KEY_SERVICES.items()
    }
    for artist in _ARTIST_CORPUS:
        keys = {svc: fn(artist) for svc, fn in funcs.items()}
        assert len(set(keys.values())) == 1, (
            f"_artist_key disagrees on {artist!r}: {keys}"
        )
    # The canonical (transliterate) form folds accents rather than dropping them.
    for svc, fn in funcs.items():
        assert fn("Motörhead") == fn("Motorhead"), (
            f"{svc}: _artist_key does not transliterate ('Motörhead' != 'Motorhead')"
        )


# --- FR-1d: _extract_title_from_filename parity -----------------------------

_EXTRACT_SERVICES = {
    "charts-songrequest": ("library.py",),
    "charts-energy": ("presence.py",),
    "charts-sync": ("presence.py",),
    "charts-import": ("importer.py",),
}


def test_extract_title_parity():
    funcs = {
        svc: _load(svc, rel)._extract_title_from_filename
        for svc, (rel,) in _EXTRACT_SERVICES.items()
    }
    cases = {
        "Artist - Greatest Hits - 05 - Into You.mp3": "Into You",
        "Into You.mp3": "Into You",
    }
    for filename, expected in cases.items():
        results = {svc: fn(filename) for svc, fn in funcs.items()}
        assert len(set(results.values())) == 1, (
            f"_extract_title_from_filename disagrees on {filename!r}: {results}"
        )
        # Document the shared expectation too.
        assert set(results.values()) == {expected}, (
            f"_extract_title_from_filename({filename!r}) != {expected!r}: {results}"
        )


# --- FR-1e: _looks_like_compilation parity ----------------------------------

_LOOKS_SERVICES = ["charts-sync", "charts-energy",
                   "charts-flashback", "charts-recommend", "charts-hitparade"]


def test_looks_like_compilation_parity():
    import os
    sep = os.sep
    funcs = {
        svc: _load(svc, "library.py")._looks_like_compilation
        for svc in _LOOKS_SERVICES
    }
    # A Lidarr "(YYYY)" album folder is a curated single-artist album even when
    # its name trips a marker word -> must read False everywhere.
    year_path = sep.join(
        ["", "music", "Artist", "Greatest Hits (2008)",
         "Artist - Greatest Hits - 05 - Into You.mp3"])
    # A genuine non-year sampler -> must read True everywhere.
    comp_path = sep.join(
        ["", "music", "Various Artists", "Bravo Hits 2020",
         "VA - 05 - X.mp3"])
    for svc, fn in funcs.items():
        assert fn(year_path, "Greatest Hits", "Artist") is False, (
            f"{svc}: Lidarr '(YYYY)' album wrongly read as compilation"
        )
        assert fn(comp_path, "Bravo Hits 2020", "Various Artists") is True, (
            f"{svc}: non-year sampler not read as compilation"
        )


# --- FR-1f: matching._COMPILATION_MARKERS set-equality ----------------------

_MARKER_SERVICES = ["charts-download", "charts-songrequest", "charts-upgrade"]


def test_compilation_markers_set_parity():
    sets = {
        svc: set(_load(svc, "matching.py")._COMPILATION_MARKERS)
        for svc in _MARKER_SERVICES
    }
    ref = sets["charts-download"]
    for svc, s in sets.items():
        assert s == ref, (
            f"{svc}: matching._COMPILATION_MARKERS differs from charts-download "
            f"(only in {svc}: {s - ref}; missing: {ref - s})"
        )


# --- FR-1g: quality table + cutoff boundary parity --------------------------

_QUALITY_SERVICES = ["charts-songrequest", "charts-upgrade", "charts-import"]


def test_quality_table_and_boundaries():
    mods = {svc: _load(svc, "quality.py") for svc in _QUALITY_SERVICES}
    # Table equality (list of (min_bitrate, cutoff_khz) pairs).
    tables = {svc: m._EXPECTED_CUTOFF_BY_BITRATE for svc, m in mods.items()}
    ref = tables["charts-import"]
    for svc, tbl in tables.items():
        assert tbl == ref, f"{svc}: _EXPECTED_CUTOFF_BY_BITRATE differs: {tbl}"
    # Boundary parity of _expected_cutoff_khz. is_upsampled() decodes audio via
    # ffmpeg (takes a file PATH), so it is NOT exercised here; the table + these
    # boundaries fully pin its decision logic without any audio decode.
    expected = {320: 19.0, 256: 18.0, 192: 17.0, 160: 15.5, 128: 15.0, 100: 0.0}
    for bitrate, want in expected.items():
        results = {svc: m._expected_cutoff_khz(bitrate) for svc, m in mods.items()}
        assert set(results.values()) == {want}, (
            f"_expected_cutoff_khz({bitrate}) != {want}: {results}"
        )
