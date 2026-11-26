"""
i18n tests for charts-dashboard (Phase 4).

Mirrors the sister services' LOCAL parity guard (identical key sets, no empty
values, {name} placeholder parity via string.Formatter) over the shared central
lang/*.json files, so the dashboard CI run is green independently of charts-sync.
The authoritative central guard lives in charts-sync/tests/test_i18n.py and
already covers the new dashboard.* keys; this file does NOT weaken the {name}
check.

Added ADDITIVE dashboard tests (design Decision 3):
  - test_ui_subset_is_json_safe: every dashboard.ui.* value is safe to inject
    into a <script> element (no </script, <!--, --> terminators). Inert
    <b>/<code> markup is allowed on purpose.
  - test_dashboard_ui_keys_present: canary set of UI keys the frontend depends on.
  - test_de_golden_dashboard: byte-identical German anchors incl. the
    entity/path and ellipsis canaries.

Plus a FastAPI TestClient render proof of the server->frontend handoff for
de/en/fr (<html lang>, parseable const I18N, a known placeholder key, and the
Donate footer on/off per DASHBOARD_DONATE). All tests run WITHOUT network/Docker.
"""

import importlib
import json
import pathlib
import re
import shutil
import string
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import i18n


# Central shared lang files: app -> charts-dashboard -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")
_UI_PREFIX = "dashboard.ui."


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- Local parity guard (second line of defence) ----------------------------

def test_shipped_key_sets_are_identical():
    key_sets = {c: set(_load_shipped(c)) for c in _SHIPPED}
    assert key_sets["en"] == key_sets["de"] == key_sets["fr"], (
        f"key sets diverge: en^de={key_sets['en'] ^ key_sets['de']}, "
        f"en^fr={key_sets['en'] ^ key_sets['fr']}")


def test_shipped_no_empty_values():
    for c in _SHIPPED:
        for key, value in _load_shipped(c).items():
            assert value != "", f"empty value for {key} in {c}.json"


def test_shipped_placeholder_parity_per_key():
    tables = {c: _load_shipped(c) for c in _SHIPPED}
    for key in tables["en"]:
        ph = {c: _placeholders(tables[c][key]) for c in _SHIPPED}
        assert ph["en"] == ph["de"] == ph["fr"], (
            f"placeholder mismatch for key '{key}': {ph}")


# --- Additive dashboard tests -----------------------------------------------

def test_ui_subset_is_json_safe():
    """Every dashboard.ui.* value must be safe inside a <script> literal.

    Guards the three HTML-parser script/comment terminators (case-insensitive)
    that would break the injected const I18N. Inert <b>/<code> markup and {name}
    placeholders / UTF-8 accents stay allowed (see Injection safety).
    """
    banned = ("</script", "<!--", "-->")
    for c in _SHIPPED:
        for key, value in _load_shipped(c).items():
            if not key.startswith(_UI_PREFIX):
                continue
            low = value.lower()
            for b in banned:
                assert b not in low, f"{key} in {c}.json contains banned {b!r}"


def test_dashboard_ui_keys_present():
    """Canary set the frontend hard-depends on (full dotted keys)."""
    canaries = {
        "dashboard.ui.updated.loading",
        "dashboard.ui.updated.refreshed",
        "dashboard.ui.time.never",
        "dashboard.ui.time.unit.min",
        "dashboard.ui.time.in_prefix",
        "dashboard.ui.tile.reachable",
        "dashboard.ui.tile.not_reachable",
        "dashboard.ui.donate.label",
    }
    for c in _SHIPPED:
        keys = set(_load_shipped(c))
        missing = canaries - keys
        assert not missing, f"missing UI canary keys in {c}.json: {missing}"


def test_de_golden_dashboard():
    """CHARTS_LANG=de reproduces today's German byte-exact (incl. entity/path
    and ellipsis canaries that lock Decision 1b / rev-2 Findings 1-2)."""
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # textContent path -> DECODED '&'
    assert (i18n.t("dashboard.ui.brand.subtitle")
            == "Charts-Automatik \u00b7 Status & Verbindungen")
    # innerHTML path -> ENCODED '&amp;'
    assert i18n.t("dashboard.ui.stage.report") == "\u00dcBERWACHUNG &amp; REPORT"
    # ellipsis preserved inside the no-since running key
    assert i18n.t("dashboard.ui.svc.running") == "\u23f3 l\u00e4uft gerade\u2026"
    # representative prose anchors
    assert (i18n.t("dashboard.ui.tile.slskd.connected")
            == "verbunden & eingeloggt")
    assert (i18n.t("dashboard.conn.flashback.bad_hash")
            == "Hash ung\u00fcltig (API ge\u00e4ndert?)")
    # shared template keeps its {code} placeholder
    assert i18n.t("dashboard.conn.http_error", code=503) == "Fehler (HTTP 503)"


@pytest.mark.parametrize("lang", _SHIPPED)
def test_connection_tiles_configuration_and_failure_details(lang):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise the dashboard JavaScript")
    html = (pathlib.Path(i18n.__file__).parent / "static" / "index.html").read_text(
        encoding="utf-8")
    tile = re.search(r"function tile\(cls, title, lines\) \{[\s\S]*?\n\}", html)
    connections = re.search(
        r"const ext = d\.external \|\| \{\};[\s\S]*?"
        r'groupBlock\("conn\.group\.helpers", helpers\);', html)
    translator = re.search(r"function t\(key, params\) \{[\s\S]*?\n\}", html)
    assert tile and connections and translator
    messages = _load_shipped(lang)
    optional = ("slskd", "lidarr", "apple", "lastfm", "flashback", "energy",
                "hitparade", "musicbrainz", "pushover")
    reasons = {
        "slskd": "dashboard.conn.slskd.no_api_key",
        "lidarr": "dashboard.conn.lidarr.not_configured",
        "lastfm": "dashboard.conn.lastfm.no_api_key",
        "flashback": "dashboard.conn.flashback.no_hash",
        "pushover": "dashboard.conn.pushover.not_configured",
    }
    cases = []
    timeout_detail = messages["dashboard.conn.unreachable"].format(err="ReadTimeout")
    for configured, ok, error in (
            (False, False, "HTTP 503"), (True, True, ""),
            (True, False, "HTTP 503"), (True, False, timeout_detail)):
        data = {"external": {
            "deezer": {"ok": False, "detail": error},
            "listenbrainz": {"ok": False, "detail": error},
            "itunes": {"ok": False, "detail": error},
        }}
        for service in optional:
            detail = (messages[reasons.get(service, "dashboard.conn.not_in_use")]
                      if not configured else error)
            status = {"configured": configured, "ok": ok, "detail": detail}
            target = data if service in ("slskd", "lidarr") else data["external"]
            target[service] = status
        cases.append(data)
    payload = {
        "tile": tile.group(),
        "connections": connections.group(),
        "translator": translator.group(),
        "messages": {k.removeprefix(_UI_PREFIX): v for k, v in messages.items()
                     if k.startswith(_UI_PREFIX)},
        "cases": cases,
    }
    script = """
const vm = require("node:vm");
const input = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const results = input.cases.map(d => vm.runInNewContext(
  input.translator + "\\n" + input.tile +
  "\\nconst rendered = {}; const originalTile = tile;" +
  "tile = (cls, title, lines) => rendered[title] = originalTile(cls, title, lines);\\n" +
  input.connections + "\\nrendered",
  {d, I18N: input.messages, document: {getElementById: () => ({innerHTML: ""})}}
));
process.stdout.write(JSON.stringify(results));
"""
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(payload), text=True,
        capture_output=True, check=True, timeout=10)
    unconfigured, reachable, failed, timed_out = json.loads(result.stdout)
    for service in optional:
        title = messages[f"dashboard.ui.tile.title.{service}"]
        lines = re.findall(r'<div class="line">(.*?)</div>', unconfigured[title])
        assert 'class="tile muted"' in unconfigured[title], service
        assert lines[0] == messages["dashboard.ui.tile.not_configured"], service
        assert len(lines) == len(set(lines)), service
        assert 'class="tile ok"' in reachable[title], service
        failure_class = "warn" if service == "musicbrainz" else "err"
        assert f'class="tile {failure_class}"' in failed[title], service
        if service != "pushover":
            assert "HTTP 503" in failed[title], service
    lastfm = unconfigured[messages["dashboard.ui.tile.title.lastfm"]]
    assert lastfm.count(messages["dashboard.conn.lastfm.no_api_key"]) == 1
    for service in ("deezer", "listenbrainz"):
        title = messages[f"dashboard.ui.tile.title.{service}"]
        assert 'class="tile err"' in unconfigured[title]
        assert messages["dashboard.ui.tile.not_reachable"] in unconfigured[title]
    for service in optional + ("deezer", "listenbrainz", "itunes"):
        if service == "pushover":
            continue
        title = messages[f"dashboard.ui.tile.title.{service}"]
        assert "ReadTimeout" in timed_out[title], service
        assert timed_out[title].count(
            messages["dashboard.ui.tile.not_reachable"]) <= 1, service


# --- TestClient render proof (server->frontend handoff) ----------------------

def _client(monkeypatch, lang):
    """Build a TestClient with a stub session and the chosen language.

    Reloads main so module-level constants (login users, donate footer) and the
    i18n table pick up the per-test env. Returns (client, main_module, token).
    """
    from fastapi.testclient import TestClient
    # A password so config.APP_USERS is non-empty (main raises otherwise).
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    monkeypatch.setenv("DASHBOARD_USERNAME", "admin")
    monkeypatch.setenv("CHARTS_LANG", lang)
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang=lang, lang_dir=_LANG_DIR)
    import app.main as main_mod
    importlib.reload(main_mod)
    token = "test-token"
    main_mod._sessions[token] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", token)
    return client, main_mod


def _extract_i18n(html):
    """Parse the injected `const I18N = {...};` object from the rendered page."""
    # json.dumps emits the whole object on ONE line, so anchor the capture to
    # that line (greedy up to the trailing };) rather than a non-greedy match
    # that could stop at a '};' inside a value.
    m = re.search(r"const I18N = (\{.*\});\s*$", html, re.M)
    assert m, "const I18N object not found in rendered HTML"
    return json.loads(m.group(1))


@pytest.mark.parametrize("lang", ["de", "en", "fr"])
def test_index_render_handoff(monkeypatch, lang):
    client, main_mod = _client(monkeypatch, lang)
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert f'<html lang="{lang}">' in html
    i18n_obj = _extract_i18n(html)
    # a surviving dashboard.ui.* key is present in the client handoff
    assert "tile.slskd.connected" in i18n_obj
    # language-correct sample strings
    expected = {
        "de": "verbunden & eingeloggt",
        "en": "connected & logged in",
        "fr": "connect\u00e9 et authentifi\u00e9",
    }[lang]
    assert i18n_obj["tile.slskd.connected"] == expected
    assert 'id="loading-status"' in html
    assert "loading.detail" in i18n_obj


@pytest.mark.parametrize("stream", [False, True])
def test_slow_status_collection_keeps_health_endpoint_responsive(monkeypatch, stream):
    client, main_mod = _client(monkeypatch, "de")
    started = threading.Event()
    release = threading.Event()
    def slow_collect():
        started.set()
        if not release.wait(3):
            raise RuntimeError("test collection was not released")
        return {"services": {}}
    monkeypatch.setattr(main_mod.collector, "collect", slow_collect)
    def slow_events():
        yield {"type": "progress", "checks": ["external"]}
        yield {"type": "result", "data": slow_collect()}
    monkeypatch.setattr(main_mod.collector, "collect_events", slow_events)
    with client, ThreadPoolExecutor(max_workers=2) as executor:
        status = executor.submit(client.get, "/api/status/stream" if stream else "/api/status")
        try:
            assert started.wait(1)
            health = executor.submit(client.get, "/health")
            assert health.result(timeout=1).json() == {"status": "ok"}
        finally:
            release.set()
        response = status.result(timeout=1)
        if stream:
            assert json.loads(response.text.splitlines()[-1]) == {
                "type": "result", "data": {"services": {}}}
        else:
            assert response.json() == {"services": {}}


def test_status_stream_auth_snapshot_and_explicit_failure(monkeypatch, caplog):
    client, main_mod = _client(monkeypatch, "de")
    events = [{"type": "progress", "checks": ["external"]},
              {"type": "checked", "check": "external"},
              {"type": "result", "data": {"external": {"ok": False}}}]
    monkeypatch.setattr(main_mod.collector, "collect_events", lambda: iter(events))
    response = client.get("/api/status/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["x-accel-buffering"] == "no"
    assert [json.loads(line) for line in response.text.splitlines()] == events
    client.cookies.clear()
    assert client.get("/api/status/stream").status_code == 401
    client.cookies.set("session", "test-token")
    def failing_events():
        yield events[0]
        raise RuntimeError("test collection failure")
    monkeypatch.setattr(main_mod.collector, "collect_events", failing_events)
    response = client.get("/api/status/stream")
    assert [json.loads(line) for line in response.text.splitlines()] == [
        events[0], {"type": "error"}]
    assert "test collection failure" in caplog.text


@pytest.mark.parametrize("lang", _SHIPPED)
def test_status_polling_loading_timeout_and_retry(lang):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to exercise dashboard polling")
    html = (pathlib.Path(i18n.__file__).parent / "static" / "index.html").read_text()
    polling = re.search(r"let statusPending = false;[\s\S]*?(?=\napplyStaticI18n\(\))", html)
    translator = re.search(r"function t\(key, params\) \{[\s\S]*?\n\}", html)
    assert polling and translator
    messages = {key[len(_UI_PREFIX):]: value for key, value in _load_shipped(lang).items()
                if key.startswith(_UI_PREFIX)}
    code = "const source = " + json.dumps(translator.group() + "\n" + polling.group()) + ";\n"
    code += "const messages = " + json.dumps(messages) + ";\n"
    code += r"""
const vm = require("node:vm"), assert = require("node:assert/strict");
function state() {
  const nodes = Object.fromEntries(["loading-status", "loading-spinner",
    "loading-title", "loading-detail", "updated", "loading-checks", "loading-progress",
    ...["containers", "slskd", "lidarr", "external", "storage", "inflight"].map(k => "loading-" + k)
    ].map(id => [id, {hidden: false, textContent: "", className: "",
      state: {textContent: ""}, querySelector() {return this.state;}}]));
  const classes = new Set(), timers = new Map(), errors = [], snapshots = [];
  let id = 0, rendered = 0;
  const context = {
    I18N: messages, REFRESH: 15, Date, AbortController, TextDecoder, location: {},
    console: {error: (...args) => errors.push(args)},
    document: {getElementById: name => nodes[name],
      querySelector: () => ({classList: {
        add: name => classes.add(name), remove: name => classes.delete(name)}})},
    render: data => {rendered++; snapshots.push(data);},
    setTimeout: (fn, delay) => {timers.set(++id, {fn, delay}); return id;},
    clearTimeout: key => timers.delete(key),
    setInterval: fn => {timers.set(++id, {fn, interval: true}); return id;},
    clearInterval: key => timers.delete(key),
  };
  vm.createContext(context);
  vm.runInContext(source + "\nglobalThis.poll = tick;", context);
  return {context, nodes, classes, timers, errors, snapshots, rendered: () => rendered};
}
function response(events = [{type: "result", data: {}}], chunkSize = 7) {
  const bytes = Buffer.from(events.map(event => JSON.stringify(event)).join("\n") + "\n");
  let offset = 0;
  return {status: 200, ok: true, body: {getReader: () => ({
    async read() {
      if (offset >= bytes.length) return {done: true};
      const value = bytes.subarray(offset, offset += chunkSize);
      return {value, done: false};
    },
    releaseLock() {},
  })}};
}
(async () => {
  const s = state();
  let resolve, calls = 0, signal;
  s.context.fetch = (_url, options) => {
    calls++; signal = options.signal; return new Promise(done => resolve = done);
  };
  const pending = s.context.poll();
  await s.context.poll();
  assert.equal(calls, 1, "overlapping request");
  assert.equal(s.nodes["loading-status"].hidden, false);
  assert(s.classes.has("loading"));
  resolve(response([
    {type: "progress", checks: ["containers", "slskd", "lidarr", "external", "storage", "inflight"]},
    {type: "checked", check: "external"},
    {type: "result", data: {detail: "é"}},
  ], 1));
  await pending;
  assert.equal(s.rendered(), 1);
  assert.equal(signal.aborted, false, "completed streams must not be cancelled");
  assert.equal(s.snapshots[0].detail, "é", "UTF-8 characters must survive split chunks");
  assert.equal(s.nodes["loading-status"].hidden, true);
  assert.equal(s.classes.has("loading"), false);
  assert.equal(s.nodes["loading-external"].className, "checked");
  assert.equal(s.nodes["loading-slskd"].className, "running");
  assert.equal(s.nodes["loading-external"].state.textContent, messages["loading.checked"]);
  assert.equal(s.nodes["loading-progress"].textContent,
    messages["loading.completed"].replace("{done}", "1").replace("{total}", "6"));
  assert.equal(s.timers.size, 1, "only the next poll should remain scheduled");
  const [nextId, next] = [...s.timers.entries()][0];
  s.timers.delete(nextId);
  s.context.fetch = async () => ({status: 503, ok: false});
  await next.fn();
  assert.equal(s.rendered(), 1, "failed refresh must not replace existing data");
  assert.equal(s.nodes["loading-status"].hidden, true);
  assert(s.classes.has("error"));
  assert.equal(s.errors.length, 1);

  const failed = state();
  failed.context.fetch = async () => ({status: 503, ok: false});
  await failed.context.poll();
  assert.equal(failed.nodes["loading-title"].textContent, messages["updated.error"]);
  assert.equal(failed.nodes["loading-spinner"].hidden, true);
  assert.equal(failed.nodes["loading-status"].hidden, false);
  failed.timers.clear();
  failed.context.fetch = async () => response();
  await failed.context.poll();
  assert.equal(failed.nodes["loading-status"].hidden, true);
  assert.equal(failed.classes.has("error"), false);

  for (const events of [[{type: "error"}], [{type: "checked", check: "unknown"}], []]) {
    const broken = state();
    broken.context.fetch = async () => response(events);
    await broken.context.poll();
    assert.equal(broken.rendered(), 0, "invalid or incomplete stream must not render success");
    assert(broken.classes.has("error"));
    assert.equal(broken.nodes["loading-checks"].hidden, true);
  }

  const timeout = state();
  timeout.context.fetch = (_url, options) => new Promise((_done, reject) => {
    options.signal.addEventListener("abort", () => reject(new Error("request aborted")));
  });
  const waiting = timeout.context.poll();
  const deadline = [...timeout.timers.values()].find(timer => timer.delay === 120000);
  assert(deadline, "status request must have a two-minute deadline");
  deadline.fn();
  await waiting;
  assert.equal(timeout.nodes["loading-title"].textContent, messages["updated.error"]);
  assert(timeout.nodes["loading-detail"].textContent.includes("15"));
  assert.equal(timeout.nodes["loading-spinner"].hidden, true);
  assert.equal(timeout.timers.size, 1, "retry should remain scheduled");
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run([node], input=code, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_index_de_byte_identical_sample(monkeypatch):
    client, _ = _client(monkeypatch, "de")
    i18n_obj = _extract_i18n(client.get("/").text)
    # de must be byte-identical to today's German UI
    assert i18n_obj["brand.subtitle"] == "Charts-Automatik \u00b7 Status & Verbindungen"
    assert i18n_obj["svc.running"] == "\u23f3 l\u00e4uft gerade\u2026"


def test_donate_footer_on_by_default(monkeypatch):
    client, _ = _client(monkeypatch, "de")
    html = client.get("/").text
    assert 'class="site-footer"' in html
    assert "https://paypal.me/rogerwyss" in html
    assert "https://revolut.me/rogerwyss" in html
    assert 'rel="noopener noreferrer"' in html
    assert 'target="_blank"' in html


def test_donate_footer_off_when_disabled(monkeypatch):
    monkeypatch.setenv("DASHBOARD_DONATE", "0")
    client, _ = _client(monkeypatch, "de")
    html = client.get("/").text
    assert 'class="site-footer"' not in html
    assert "paypal.me/rogerwyss" not in html


def test_login_page_translated(monkeypatch):
    client, _ = _client(monkeypatch, "de")
    # fresh client without session cookie to hit the login page directly
    from fastapi.testclient import TestClient
    import app.main as main_mod
    anon = TestClient(main_mod.app)
    r = anon.get("/login")
    assert r.status_code == 200
    assert '<html lang="de">' in r.text
    assert "Bitte anmelden" in r.text


# Restore import-time state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
