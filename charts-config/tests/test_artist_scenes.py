"""Tests for the authenticated Mundart artist inventory and selection."""

import importlib
import json
import pathlib
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app import i18n
from app import artist_scenes


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def test_artist_filters_keep_hidden_edits_in_save_payload():
    html = pathlib.Path(artist_scenes.__file__).parent / "static" / "index.html"
    proc = subprocess.run(["node", "-e", """
const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[1], 'utf8');
const fields = {
  'artist-list-search': {value: ''},
  'artist-list-playlist': {
    value: '', children: [], get selectedOptions() {
      return this.children.filter(option => option.value === this.value);
    },
    replaceChildren(...options) { this.children = options; },
    append(option) { this.children.push(option); }
  },
  'artist-filter-enabled': {checked: false},
  'artist-scene-playlists': {checked: false},
  'artist-target-scene': {value: 'My artists'}
};
const row = (key, name, scenes, enabled) => ({
  dataset: {artistKey: key, artistName: name}, hidden: false,
  fields: {'.artist-scene': {value: scenes}, '.artist-enabled': {checked: enabled},
           '.artist-evidence': {value: ''}},
  querySelector(selector) { return this.fields[selector]; },
  contains(element) { return Object.values(this.fields).includes(element); }
});
const rows = [
  row('deezer:1', 'Gölä', 'Mundart-Pop\\nMundart-Rock', true),
  row('deezer:2', 'Gotthard', 'Mundart-Rock', true),
  row('deezer:3', 'Other', '', false)
];
const count = {textContent: ''};
let posted;
const document = {
  activeElement: null,
  getElementById(id) { return fields[id]; },
  createElement() { return {value: '', textContent: ''}; },
  querySelector(selector) { return selector === '#artist-list .artist-table-count' ? count : null; },
  querySelectorAll(selector) {
    if (selector === '#artist-list tr[data-artist-key]') return rows;
    if (selector === '#artist-list .artist-scene') return rows.map(r => r.fields['.artist-scene']);
    if (selector === '#artist-scene-downloads > div') return [];
    throw new Error('Unexpected selector: ' + selector);
  }
};
const context = vm.createContext({document,
  _artistSceneState: {filter_enabled: false, scene_filenames: {
    'Mundart-Rock': 'charts-mundart-rock.m3u', Jazz: 'charts-jazz.m3u'},
    genre_playlists: {Jazz: {genre_id: '98'}}},
  _artistSaving: false, t: (key, values) => key + JSON.stringify(values || {}),
  sceneFilenamesConflict: () => false, artistFormValues: () => new Map(),
  updateArtistSaveControls() {}, setArtistMessage() {}, artistDraft: () => new Map(),
  renderArtistCatalog() {},
  fetch: async (_url, options) => {
    posted = JSON.parse(options.body);
    return {ok: true, json: async () => ({ok: true})};
  }
});
vm.runInContext(html.slice(html.indexOf('function artistFilterKey('),
  html.indexOf('function renderArtistCatalog(')), context);
vm.runInContext(html.slice(html.indexOf('async function saveArtistSelection('),
  html.indexOf('async function showArtistPreview(')), context);
(async () => {
  context.refreshArtistPlaylistFilter();
  assert.deepEqual(fields['artist-list-playlist'].children.map(o => o.value),
    ['', 'mundart-pop', 'mundart-rock']);
  fields['artist-list-search'].value = 'gola';
  fields['artist-list-playlist'].value = 'mundart-rock';
  context.applyArtistListFilters();
  assert.deepEqual(rows.map(r => r.hidden), [false, true, true]);
  assert.match(count.textContent, /"visible":1,"total":3/);
  rows[1].fields['.artist-scene'].value = 'Mundart-Rock\\nExtra';
  await context.saveArtistSelection();
  assert.equal(posted.artists.length, 3);
  assert.deepEqual(posted.artists[1].scenes, ['Mundart-Rock', 'Extra']);
  context.refreshArtistPlaylistFilter();
  assert.equal(fields['artist-list-playlist'].value, 'mundart-rock');
  assert(fields['artist-list-playlist'].children.some(o => o.value === 'extra'));
  fields['artist-list-search'].value = '';
  document.activeElement = rows[0].fields['.artist-scene'];
  rows[0].fields['.artist-scene'].value = 'Mundart-Pop';
  context.applyArtistListFilters();
  assert.equal(rows[0].hidden, false);
  document.activeElement = null;
  context.applyArtistListFilters();
  assert.deepEqual(rows.map(r => r.hidden), [true, false, true]);
})().catch(error => { console.error(error); process.exitCode = 1; });
""", str(html)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_unusual_genre_classification_keeps_uncommon_names_selectable():
    javascript = pathlib.Path(artist_scenes.__file__).parent / "static" / "genre-playlists.js"
    proc = subprocess.run(["node", "-e", """
const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const elements = new Map();
const element = () => ({value: '', children: [], textContent: '', hidden: false,
  append(...items) { this.children.push(...items); }, replaceChildren() { this.children = []; },
  addEventListener() {}, focus() {}, scrollIntoView() {}});
const document = {getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
  createElement: element, querySelector() { return element(); }};
const state = {genre_playlists: {Saved: {genre_id: '132', genre_name: 'Pop', limit: 50}, Legacy: {genres: ['Rock']}}};
let calls = [];
const context = vm.createContext({document, _artistSceneState: state, _artistSaving: false, t: k => k,
  updateArtistSaveControls() {}, sceneFilenameKey: s => s.toLowerCase(),
  renderArtistCatalog(data) { context._artistSceneState = data; }, loadScenePlaylists: async () => true,
  fetch: async (url, options) => { calls.push([url, options]); if (!options.method) throw new Error('offline');
    return {ok: true, json: async () => ({catalog: state})}; }});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
(async () => {
  document.getElementById('genre-limit').value = '50';
  await context.loadGenreSource();
  assert.equal(document.getElementById('genre-catalog-status').textContent, 'genres.source_failed');
  assert.equal(document.getElementById('genre-save').disabled, true);
  context.editGenrePlaylist('Legacy');
  assert.equal(document.getElementById('genre-playlist-name').value, '');
  context.editGenrePlaylist('Saved');
  assert.equal(document.getElementById('genre-select').value, '132');
  assert.equal(document.getElementById('genre-select').disabled, true);
  assert.equal(document.getElementById('genre-save').disabled, false);
  assert.equal(document.getElementById('genre-playlist-name').readOnly, true);
  document.getElementById('genre-limit').value = '25';
  await context.saveGenrePlaylist({preventDefault() {}});
  assert.deepEqual(JSON.parse(calls[1][1].body), {name: 'Saved', genre_id: '132', limit: 25, replace: true});
  assert.equal(document.getElementById('genre-save').disabled, true);
  context.fetch = async () => ({ok: true, json: async () => ({ok: true, genres: [{id: '0', name: 'All'}]})});
  await context.loadGenreSource();
  assert.equal(document.getElementById('genre-catalog-status').textContent, 'genres.source_failed');
  context.fetch = async () => ({ok: true, json: async () => ({ok: true, genres: [{id: '132', name: 'Pop'}]})});
  await context.loadGenreSource();
  assert.equal(document.getElementById('genre-catalog-status').textContent, 'genres.source_ready');
  assert.equal(document.getElementById('genre-save').disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
""", str(javascript)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_scene_download_limits_persist_and_can_be_disabled(artist_data_dir):
    settings = {"filter_enabled": False, "scene_playlists": True,
                "target_scene": "Swiss German Mundart", "artists": []}
    assert artist_scenes.get_catalog()["scene_downloads"] == {}
    settings["scene_downloads"] = {"Mundart-Pop": 25, "Rock": 100}
    assert artist_scenes.save_settings(settings, "admin")["scene_downloads"] == {
        "Mundart-Pop": 25, "Rock": 100}
    for invalid in ({"Rock": True}, {"Rock": 101}, {"": 50}, {"Rock": 25, "rock": 50}):
        with pytest.raises(artist_scenes.ArtistSceneError):
            artist_scenes.save_settings({**settings, "scene_downloads": invalid}, "admin")
    assert artist_scenes.get_catalog()["scene_downloads"]["Mundart-Pop"] == 25
    assert artist_scenes.save_settings({**settings, "scene_downloads": {}},
                                       "admin")["scene_downloads"] == {}


def test_genre_groups_sort_by_count_and_preserve_selected_missing_tags():
    javascript = pathlib.Path(artist_scenes.__file__).parent / "static" / "genre-playlists.js"
    proc = subprocess.run(["node", "-e", """
const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const elements = new Map();
const pane = {inert: false};
const element = () => ({value: '', children: [], listeners: {}, textContent: '',
  dataset: {}, classList: {add() {}},
  append(...items) { this.children.push(...items); }, replaceChildren() { this.children = []; },
  addEventListener(name, fn) { this.listeners[name] = fn; },
  setAttribute() {}, removeAttribute() {}, closest() { return pane; }});
const document = {getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
  createElement: element, querySelector() { return element(); }};
const state = {genre_playlists: {}, scene_filenames: {}, reserved_playlists: ['reserved']};
let posts = [];
const context = vm.createContext({document, _artistSceneState: state, _artistSaving: false, t: k => k,
  updateArtistSaveControls() {}, sceneFilenameKey: s => s.toLowerCase(),
  refreshArtistPlaylistFilter() {},
  renderArtistCatalog(data) { context._artistSceneState = data; }, loadScenePlaylists: async () => true,
  fetch: async (url, options) => options.method === 'POST'
    ? (posts.push(JSON.parse(options.body)), {ok: true, json: async () => ({catalog: state})})
    : {ok: true, json: async () => ({ok: true, genres: [{id: '152', name: 'Rock'},
      {id: '0', name: 'All'}, {id: '132', name: 'Pop'}]})}});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
(async () => {
  document.getElementById('genre-limit').value = '50';
  context.initGenrePlaylists();
  await context.loadGenreSource();
  assert.deepEqual(document.getElementById('genre-select').children.map(x => x.value), ['', '132', '152']);
  document.getElementById('genre-playlist-name').value = 'Reserved';
  document.getElementById('genre-select').value = '132';
  document.getElementById('genre-playlist-name').listeners.input();
  assert.equal(vm.runInContext('genreEditor.dirty', context), true);
  await context.saveGenrePlaylist({preventDefault() {}});
  assert.equal(document.getElementById('genre-save-status').textContent, 'genres.name_conflict');
  document.getElementById('genre-playlist-name').value = 'Pop Mix';
  document.getElementById('genre-limit').value = '101';
  await context.saveGenrePlaylist({preventDefault() {}});
  assert.equal(posts.length, 0);
  document.getElementById('genre-limit').value = '50';
  await context.saveGenrePlaylist({preventDefault() {}});
  assert.deepEqual(posts, [{name: 'Pop Mix', genre_id: '132', limit: 50, replace: false}]);
  assert.equal(vm.runInContext('genreEditor.dirty', context), false);
  state.scene_filenames = {'Pop Mix': 'pop mix'};
  document.getElementById('genre-playlist-name').value = 'Pop Mix';
  document.getElementById('genre-select').value = '132';
  document.getElementById('artist-target-scene').value = 'Unsaved artist draft';
  document.getElementById('genre-playlist-name').listeners.input();
  const index = fs.readFileSync(process.argv[2], 'utf8');
  vm.runInContext(index.slice(index.indexOf('async function loadScenePlaylists('),
    index.indexOf('function initArtistScenes()')), context);
  context.fetch = async (url, options) => options.method === 'POST'
    ? (posts.push(JSON.parse(options.body)), {ok: true, json: async () => ({catalog: state})})
    : {ok: true, json: async () => ({playlists: [], catalog: {
      scene_filenames: {}, genre_playlists: {}, reserved_playlists: []}})};
  assert.equal(await context.loadScenePlaylists(null, true), true);
  assert.deepEqual(Object.keys(context._artistSceneState.scene_filenames), []);
  assert.equal(document.getElementById('genre-playlist-name').value, 'Pop Mix');
  assert.equal(document.getElementById('genre-select').value, '132');
  assert.equal(document.getElementById('artist-target-scene').value, 'Unsaved artist draft');
  assert.equal(vm.runInContext('genreEditor.dirty', context), true);
  assert.equal(pane.inert, false);
  let resolveFetch;
  context.fetch = () => new Promise(resolve => { resolveFetch = resolve; });
  const pending = context.loadScenePlaylists(null, true);
  assert.equal(pane.inert, true);
  const newer = {...context._artistSceneState, scene_filenames: {Newer: 'newer'}};
  context._artistSceneState = newer;
  resolveFetch({ok: true, json: async () => ({playlists: [], catalog: {
    scene_filenames: {}, genre_playlists: {}, reserved_playlists: []}})});
  assert.equal(await pending, false);
  assert.equal(context._artistSceneState, newer);
  assert.equal(pane.inert, false);
  context.fetch = async (url, options) => {
    posts.push(JSON.parse(options.body));
    return {ok: true, json: async () => ({catalog: state})};
  };
  // Avoid the post-save overview request affecting the collision regression.
  context.loadScenePlaylists = async () => true;
  await context.saveGenrePlaylist({preventDefault() {}});
  assert.equal(posts.length, 2);
  assert.equal(posts[1].name, 'Pop Mix');
})().catch(error => { console.error(error); process.exitCode = 1; });
""", str(javascript), str(javascript.parent / "index.html")], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.fixture
def artist_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(artist_scenes.config, "ARTIST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(artist_scenes.config, "MUSIC_DIR", str(tmp_path))
    return tmp_path


def test_generic_filename_collisions_and_legacy_migration_preview(artist_data_dir):
    _write_inventory(artist_data_dir, {
        "deezer:123": {"name": "Example", "deezer_id": "123", "source_counts": {}},
    })
    payload = {"filter_enabled": False, "scene_playlists": True,
               "target_scene": "Swiss German Mundart", "artists": [
                   {"key": "deezer:123", "enabled": True, "scenes": ["French Rock"]},
               ]}
    saved = artist_scenes.save_settings(payload, "admin")
    assert saved["scene_filenames"]["French Rock"] == "charts-french-rock.m3u"
    before = (artist_data_dir / ".charts-artist-selection.json").read_text()
    payload["artists"][0]["scenes"] = ["Rock & Pop", "Rock-Pop"]
    with pytest.raises(artist_scenes.ArtistSceneError) as error:
        artist_scenes.save_settings(payload, "admin")
    assert error.value.code == "scene_name_conflict"
    assert (artist_data_dir / ".charts-artist-selection.json").read_text() == before
    (artist_data_dir / "charts-jazz.m3u").write_text("#EXTM3U\n#PLAYLIST:My own jazz\n")
    payload["artists"][0]["scenes"] = ["Jazz"]
    with pytest.raises(artist_scenes.ArtistSceneError):
        artist_scenes.save_settings(payload, "admin")
    legacy = artist_scenes._legacy_scene_filename("Mundart-Pop")
    (artist_data_dir / legacy).write_text(
        "#EXTM3U\n#PLAYLIST:Mundart: Mundart-Pop\n#CHARTS-STACK:MUNDART-SCENE\n")
    payload["artists"][0]["scenes"] = ["Mundart-Pop"]
    saved = artist_scenes.save_settings(payload, "admin")
    assert saved["scene_filenames"]["Mundart-Pop"] == "charts-mundart-pop.m3u"
    assert artist_scenes.list_scene_playlists()["playlists"][-1]["renameable"]


def test_generic_scene_reservations_and_literal_titles(artist_data_dir):
    _write_inventory(artist_data_dir, {
        "deezer:123": {"name": "Example", "deezer_id": "123", "source_counts": {}},
    })
    payload = {"filter_enabled": False, "scene_playlists": True,
               "target_scene": "Swiss German Mundart", "artists": [
                   {"key": "deezer:123", "enabled": True, "scenes": ["Mundart: Jazz"]},
               ]}
    (artist_data_dir / "charts-mundart-jazz.m3u").write_text(
        "#EXTM3U\n#PLAYLIST:Mundart: Jazz\n#CHARTS-STACK:MUNDART-SCENE\n")
    saved = artist_scenes.save_settings(payload, "admin")
    assert saved["scene_filenames"]["Mundart: Jazz"] == "charts-mundart-jazz.m3u"
    selection = json.loads((artist_data_dir / ".charts-artist-selection.json").read_text())
    selection["disabled_scene_playlists"] = {"charts-rock-pop.m3u": "Rock & Pop"}
    (artist_data_dir / ".charts-artist-selection.json").write_text(json.dumps(selection))
    assert artist_scenes.get_catalog()["scene_filenames"]["Rock & Pop"] == "charts-rock-pop.m3u"
    payload["artists"][0]["scenes"] = ["Rock-Pop"]
    with pytest.raises(artist_scenes.ArtistSceneError) as error:
        artist_scenes.save_settings(payload, "admin")
    assert error.value.code == "scene_name_conflict"


def _write_inventory(directory, artists):
    (directory / ".charts-artist-inventory.json").write_text(
        json.dumps({"version": 1, "artists": artists}), encoding="utf-8")


def _write_genre_catalog(directory):
    data = {"version": 1, "artists": {}, "genre_catalog": {
        "updated_at": "2026-11-22T12:00:00+00:00", "total": 4, "untagged": 1,
        "unreadable": 0, "reserved_playlists": ["charts-reserved.m3u"],
        "genres": [{"name": "Rock", "count": 2}, {"name": "Pop", "count": 2}],
    }}
    (directory / ".charts-artist-inventory.json").write_text(json.dumps(data))


def test_permanent_deletion_retires_queue_and_allows_recreation(artist_data_dir):
    directory = artist_data_dir
    selection = {**artist_scenes._default_selection(), "scene_playlists": True,
                 "artists": {
                     "deezer:1": {"name": "Artist", "deezer_id": "1", "enabled": True,
                                  "scene": "Old", "scenes": ["Old", "Other"], "manual": True},
                 }, "scene_downloads": {"Old": 25, "Other": 10},
                 "disabled_scene_playlists": {"charts-old.m3u": "Old"},
                 "scene_files": {"charts-old.m3u": "Old"}}
    path = directory / ".charts-artist-selection.json"
    path.write_text(json.dumps(selection))
    m3u = directory / "charts-old.m3u"
    m3u.write_text("#EXTM3U\n#PLAYLIST:Old\n#CHARTS-STACK:MUNDART-SCENE\nsong.mp3\n")
    result = artist_scenes.delete_scene_playlist(m3u.name)
    assert result["playlists"][0]["deleting"] is True
    saved = json.loads(path.read_text())
    assert saved["artists"]["deezer:1"]["scenes"] == ["Other"]
    assert saved["artists"]["deezer:1"]["enabled"] is True
    assert saved["scene_downloads"] == {"Other": 10}
    assert saved["disabled_scene_playlists"] == saved["scene_files"] == {}
    assert m3u.exists()
    with pytest.raises(artist_scenes.ArtistSceneError, match="playlist_delete_pending"):
        artist_scenes.save_settings({
            "filter_enabled": False, "target_scene": "My artists", "artists": [
                {"key": "deezer:1", "enabled": True, "scenes": ["Old", "Other"]}]}, "admin")
    sync = pathlib.Path(artist_scenes.__file__).parents[2] / "charts-sync"
    result = subprocess.run(
        [sys.executable, "-c", "from app import main; main._apply_scene_playlist_changes()"],
        cwd=sync, env={**os.environ, "CHARTS_DIR": str(directory),
                       "MUSIC_DIR": str(directory), "PLAYLIST_DIR": str(directory)},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not m3u.exists()
    assert "pending_scene_deletions" not in json.loads(path.read_text())
    assert artist_scenes.list_scene_playlists()["playlists"] == []
    artist_scenes.save_settings({
        "filter_enabled": False, "target_scene": "My artists", "artists": [
            {"key": "deezer:1", "enabled": True, "scenes": ["Old", "Other"]}]}, "admin")


def test_deleted_legacy_entry_can_be_removed_without_a_permanent_block(artist_data_dir):
    name = artist_scenes._legacy_scene_filename("Old")
    selection = {**artist_scenes._default_selection(),
                 "disabled_scene_playlists": {name: "Mundart: Old"}}
    path = artist_data_dir / ".charts-artist-selection.json"
    path.write_text(json.dumps(selection))
    assert artist_scenes.delete_scene_playlist(name)["playlists"] == []
    saved = json.loads(path.read_text())
    assert saved["disabled_scene_playlists"] == {}
    assert saved["pending_scene_deletions"] == {}


def test_permanent_delete_protects_main_filter_and_other_managed_owners(artist_data_dir):
    selection = {**artist_scenes._default_selection(), "filter_enabled": True,
                 "target_scene": "Rock", "artists": {}}
    path = artist_data_dir / ".charts-artist-selection.json"
    path.write_text(json.dumps(selection))
    m3u = artist_data_dir / "charts-rock.m3u"
    m3u.write_text("#EXTM3U\n#PLAYLIST:Rock\n#CHARTS-STACK:MUNDART-SCENE\n")
    before = path.read_text()
    with pytest.raises(artist_scenes.ArtistSceneError, match="playlist_main_filter"):
        artist_scenes.delete_scene_playlist(m3u.name)
    assert path.read_text() == before and m3u.exists()
    selection["filter_enabled"] = False
    selection["disabled_scene_playlists"] = {
        artist_scenes._legacy_scene_filename("Rock & Pop"): "Mundart: Rock & Pop"}
    path.write_text(json.dumps(selection))
    other = artist_data_dir / "charts-rock-pop.m3u"
    other.write_text("#EXTM3U\n#PLAYLIST:Rock-Pop\n#CHARTS-STACK:MUNDART-SCENE\n")
    with pytest.raises(artist_scenes.ArtistSceneError, match="scene_name_conflict"):
        artist_scenes.delete_scene_playlist(artist_scenes._legacy_scene_filename("Rock & Pop"))
    assert other.exists()


def test_genre_rules_validate_names_tags_and_persist_without_downloads(artist_data_dir):
    _write_genre_catalog(artist_data_dir)
    result = artist_scenes.save_genre_playlist({"name": "Rock Mix", "genres": ["rock", "Pop"]})
    assert result["catalog"]["genre_playlists"] == {"Rock Mix": {"genres": ["Rock", "Pop"]}}
    assert result["catalog"]["scene_downloads"] == {}
    assert result["playlists"][0]["kind"] == "genre"
    assert result["playlists"][0]["present"] is False
    for payload in ({"name": "Rock Mix", "genres": ["Rock"]},
                    {"name": "Unknown", "genres": ["Unknown"]},
                    {"name": "Reserved", "genres": ["Rock"]},
                    {"name": "Empty", "genres": []}):
        with pytest.raises(artist_scenes.ArtistSceneError):
            artist_scenes.save_genre_playlist(payload)
    artist_scenes.save_genre_playlist({"name": "Rock Mix", "genres": ["Pop"], "replace": True})
    assert artist_scenes.get_catalog()["genre_playlists"]["Rock Mix"]["genres"] == ["Pop"]
    assert artist_scenes.delete_scene_playlist("charts-rock-mix.m3u")["playlists"] == []


def test_migrated_aliases_do_not_replace_another_scene(artist_data_dir):
    aliases = {
        "mundart-szene-rock-pop-0123456789.m3u": "Rock & Pop",
        "charts-rock-pop.m3u": "Rock-Pop",
    }
    selection = {**artist_scenes._default_selection(), "scene_files": aliases}
    (artist_data_dir / ".charts-artist-selection.json").write_text(json.dumps(selection))
    (artist_data_dir / "charts-rock-pop.m3u").write_text(
        "#EXTM3U\n#PLAYLIST:Rock-Pop\n#CHARTS-STACK:MUNDART-SCENE\n")
    assert artist_scenes._load_selection()["scene_files"] == aliases
    with pytest.raises(artist_scenes.ArtistSceneError):
        artist_scenes.save_settings({
            "filter_enabled": False, "scene_playlists": True,
            "target_scene": "Swiss German Mundart", "artists": [],
        }, "admin")
    assert json.loads((artist_data_dir / ".charts-artist-selection.json").read_text()) == selection


def test_catalog_marks_source_names_without_deezer_id_pending(artist_data_dir, monkeypatch):
    monkeypatch.setattr(artist_scenes.config, "CHARTS_ARTIST_PLAYLIST", "chanson.m3u")
    _write_inventory(artist_data_dir, {
        "name:energy artist": {
            "key": "name:energy artist",
            "name": "Energy Artist",
            "deezer_id": "",
            "source_counts": {"energy:mundart": {"tracks": 2}},
        },
    })

    catalog = artist_scenes.get_catalog()

    assert catalog["playlist"] == "chanson.m3u"
    assert catalog["filter_enabled"] is False
    assert catalog["artists"][0]["identity_verified"] is False
    assert catalog["artists"][0]["enabled"] is False
    assert catalog["artists"][0]["source_counts"]["energy:mundart"]["tracks"] == 2


def test_link_and_enable_artist_persists_verified_scene_selection(artist_data_dir):
    key = "name:energy artist"
    _write_inventory(artist_data_dir, {
        key: {"key": key, "name": "Energy Artist", "deezer_id": "",
              "source_counts": {}},
    })
    artist_scenes.link_artist(key, "123", "Canonical Artist")

    result = artist_scenes.save_settings({
        "filter_enabled": True,
        "target_scene": "Swiss German Mundart",
        "artists": [{
            "key": key, "enabled": True,
            "scene": "Swiss German Mundart",
            "evidence": "Official artist biography",
        }],
    }, "admin")

    assert result["filter_enabled"] is True
    artist = result["artists"][0]
    assert artist["name"] == "Canonical Artist"
    assert artist["deezer_id"] == "123"
    assert artist["enabled"] is True
    assert artist["reviewed_by"] == "admin"
    assert artist["reviewed_at"]
    saved = json.loads(
        (artist_data_dir / ".charts-artist-selection.json").read_text(
            encoding="utf-8"))
    assert saved["artists"][key]["evidence"] == "Official artist biography"


def test_unverified_artist_cannot_be_enabled(artist_data_dir):
    key = "name:unverified"
    _write_inventory(artist_data_dir, {
        key: {"key": key, "name": "Unverified", "deezer_id": "",
              "source_counts": {}},
    })

    with pytest.raises(artist_scenes.ArtistSceneError) as error:
        artist_scenes.save_settings({
            "filter_enabled": False,
            "target_scene": "Swiss German Mundart",
            "artists": [{
                "key": key, "enabled": True,
                "scene": "Swiss German Mundart",
            }],
        }, "admin")

    assert error.value.code == "artist_not_verified"


def test_multiple_scenes_preserve_legacy_selection_and_allow_second_target(artist_data_dir):
    key = "deezer:78451"
    selection = artist_scenes._default_selection()
    selection["artists"][key] = {
        "name": "Adrian Stern", "deezer_id": "78451", "enabled": True,
        "manual": True, "scene": "Mundart-Pop",
    }
    artist_scenes._atomic_write(str(artist_data_dir / ".charts-artist-selection.json"), selection)
    assert artist_scenes.get_catalog()["artists"][0]["scenes"] == ["Mundart-Pop"]
    row = {"key": key, "enabled": True,
           "scenes": ["Mundart-Pop", "Swiss German Mundart", "mundart-pop"]}
    payload = {"filter_enabled": True, "target_scene": "Swiss German Mundart",
               "artists": [row]}
    artist_scenes.save_settings(payload, "admin")
    assert artist_scenes.get_catalog()["artists"][0]["scenes"] == [
        "Mundart-Pop", "Swiss German Mundart",
    ]
    row["scenes"] = "Swiss German Mundart"
    with pytest.raises(artist_scenes.ArtistSceneError, match="invalid_payload"):
        artist_scenes.save_settings(payload, "admin")


def test_manual_artist_limit_applies_when_promoting_existing_selection(
        artist_data_dir):
    selection = artist_scenes._default_selection()
    selection["artists"] = {
        f"deezer:{index}": {
            "deezer_id": str(index), "manual": True,
        }
        for index in range(1, artist_scenes.MAX_MANUAL_ARTISTS + 1)
    }
    selection["artists"]["deezer:999"] = {
        "deezer_id": "999", "manual": False,
    }
    (artist_data_dir / ".charts-artist-selection.json").write_text(
        json.dumps(selection), encoding="utf-8")

    with pytest.raises(artist_scenes.ArtistSceneError) as error:
        artist_scenes.add_manual_artist("999", "New Manual Artist")

    assert error.value.code == "too_many_manual"


def _load_main(monkeypatch, data_dir):
    monkeypatch.setenv("CONFIG_PASSWORD", "secret")
    monkeypatch.setenv("CONFIG_USERNAME", "admin")
    monkeypatch.setenv("ARTIST_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MUSIC_DIR", str(data_dir))
    monkeypatch.setenv("CHARTS_LANG", "en")
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod


def test_artist_routes_require_auth(tmp_path, monkeypatch):
    main_mod = _load_main(monkeypatch, tmp_path)
    client = TestClient(main_mod.app)

    requests = [
        ("get", "/api/mundart/artists", None),
        ("get", "/api/mundart/scene-playlists", None),
        ("post", "/api/mundart/scene-playlists", {}),
        ("get", "/api/mundart/artist-preview?artist_id=78451", None),
        ("post", "/api/mundart/settings", {}),
        ("post", "/api/mundart/artists/link", {}),
        ("post", "/api/mundart/artists/add", {}),
    ]
    for method, url, payload in requests:
        response = getattr(client, method)(
            url, json=payload) if payload is not None else client.get(url)
        assert response.status_code == 401


def test_artist_writes_require_json_media_type(tmp_path, monkeypatch):
    main_mod = _load_main(monkeypatch, tmp_path)
    main_mod._sessions["token"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "token")
    url = "/api/mundart/settings"
    payload = {"filter_enabled": False, "target_scene": "Mundart-Pop", "artists": []}
    for headers in ({}, {"Content-Type": "text/plain"}):
        rejected = client.post(url, content=json.dumps(payload), headers=headers)
        assert rejected.status_code == 415
    assert artist_scenes.get_catalog()["target_scene"] != "Mundart-Pop"
    accepted = client.post(url, content=json.dumps(payload),
                           headers={"Content-Type": "application/json; charset=utf-8"})
    assert accepted.status_code == 200
    assert accepted.json()["target_scene"] == "Mundart-Pop"


def test_authenticated_routes_link_and_save_artist(tmp_path, monkeypatch):
    main_mod = _load_main(monkeypatch, tmp_path)
    key = "name:energy artist"
    _write_inventory(tmp_path, {
        key: {"key": key, "name": "Energy Artist", "deezer_id": "",
              "source_counts": {}},
    })
    monkeypatch.setattr(
        main_mod.collector, "verify_deezer_artist",
        lambda artist_id: {"ok": True, "id": artist_id, "name": "Canonical Artist"},
    )
    main_mod._sessions["token"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "token")

    linked = client.post("/api/mundart/artists/link",
                         json={"key": key, "deezer_id": "123"})
    assert linked.status_code == 200
    assert linked.json()["artists"][0]["deezer_id"] == "123"

    saved = client.post("/api/mundart/settings", json={
        "filter_enabled": True,
        "scene_playlists": True,
        "target_scene": "Swiss German Mundart",
        "artists": [{
            "key": key, "enabled": True, "scene": "Swiss German Mundart",
            "evidence": "Official artist biography",
        }],
    })
    assert saved.status_code == 200
    assert saved.json()["filter_enabled"] is True
    assert client.get("/api/mundart/artists").json()["scene_playlists"] is True
    assert saved.json()["artists"][0]["enabled"] is True


def test_artist_preview_uses_only_official_deezer_audio(tmp_path, monkeypatch):
    import httpx

    main_mod = _load_main(monkeypatch, tmp_path)
    main_mod._sessions["token"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "token")
    tracks = [
        {"title": "Wrong host", "preview": "https://cdnt-preview.dzcdn.net.example/a.mp3"},
        {"title": "Amerika", "preview": "https://cdnt-preview.dzcdn.net/b.mp3?signature=test"},
        {"title": "Amerika", "preview": "https://cdnt-preview.dzcdn.net/c.mp3"},
        {"title": "No preview", "preview": ""},
        {"title": "Meer", "preview": "https://cdn-preview.dzcdn.net/d.mp3"},
    ]

    def get(url, **kwargs):
        assert url == "https://api.deezer.com/artist/78451/top"
        return httpx.Response(200, json={"data": tracks}, request=httpx.Request("GET", url))

    monkeypatch.setattr(main_mod.collector.httpx, "get", get)
    url = "/api/mundart/artist-preview?artist_id=78451"
    result = client.get(url)
    assert result.json() == {"ok": True, "previews": [
        {"title": "Amerika", "url": tracks[1]["preview"]},
        {"title": "Meer", "url": tracks[4]["preview"]},
    ]}
    assert result.headers["cache-control"] == "no-store"
    del tracks[1:]
    assert client.get(url).status_code == 404
    assert client.get("/api/mundart/artist-preview?artist_id=invalid").status_code == 404


def test_playlist_overview_queues_deletion_without_changing_assignments(tmp_path, monkeypatch):
    main_mod = _load_main(monkeypatch, tmp_path)
    monkeypatch.setattr(artist_scenes.config, "MUSIC_DIR", str(tmp_path))
    name = "mundart-szene-pop-1234567890.m3u"
    generated = tmp_path / name
    generated.write_text("#EXTM3U\n#PLAYLIST:Mundart: Pop\n#CHARTS-STACK:MUNDART-SCENE\n")
    (tmp_path / "mundart-szene-local-1234567890.m3u").write_text("#EXTM3U\n")
    (tmp_path / "mundart-szene-link-1234567890.m3u").symlink_to(generated)
    selection_path = tmp_path / ".charts-artist-selection.json"
    selection = artist_scenes._default_selection()
    selection["artists"]["deezer:42"] = {"deezer_id": "42", "enabled": True, "scene": "Pop"}
    selection_path.write_text(json.dumps(selection))
    main_mod._sessions["token"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "token")
    url = "/api/mundart/scene-playlists"
    listed = client.get(url).json()["playlists"]
    assert [item["name"] for item in listed] == [name]
    assert client.post(url, json={"name": "../" + name, "disabled": True}).status_code == 400
    queued = client.post(url, json={"name": name, "disabled": True})
    assert queued.json()["playlists"][0] == {
        "name": name, "title": "Mundart: Pop", "present": True, "disabled": True,
    }
    assert json.loads(selection_path.read_text())["artists"] == selection["artists"]
    assert client.post(url, json={"name": name, "disabled": False}).json()["playlists"][0]["disabled"] is False


def test_rename_scene_migrates_settings_without_relinking(tmp_path, monkeypatch):
    main_mod = _load_main(monkeypatch, tmp_path)
    monkeypatch.setattr(artist_scenes.config, "MUSIC_DIR", str(tmp_path))
    name = "mundart-szene-swiss-german-mundart-34e7fd1866.m3u"
    old = "Swiss German Mundart"
    new = "Dialekt"
    marker = f"#EXTM3U\n#PLAYLIST:Mundart: {old}\n#CHARTS-STACK:MUNDART-SCENE\n"
    (tmp_path / name).write_text(marker)
    artist = {"name": "Example", "deezer_id": "42", "enabled": True, "manual": True,
              "evidence": "Verified", "scene": old, "scenes": [old, "Pop"]}
    selection = {**artist_scenes._default_selection(), "target_scene": old,
                 "scene_downloads": {old: 35, "Pop": 15},
                 "artists": {"deezer:42": artist}}
    state = tmp_path / ".charts-artist-selection.json"
    state.write_text(json.dumps(selection))
    client = TestClient(main_mod.app)
    url = "/api/mundart/scene-playlists"
    assert client.post(url, json={"name": name, "new_scene": new}).status_code == 401
    main_mod._sessions["token"] = "admin"
    client.cookies.set("session", "token")
    assert client.get(url).json()["playlists"][0]["renameable"]
    assert client.post(url, json={"name": name, "new_scene": "pop"}).status_code == 409
    assert json.loads(state.read_text()) == selection
    response = client.post(url, json={"name": name, "new_scene": new})
    assert response.status_code == 200
    assert response.json()["catalog"]["scene_downloads"] == {new: 35, "Pop": 15}
    saved = json.loads(state.read_text())
    assert saved["target_scene"] == new
    assert saved["scene_files"] == {name: new}
    assert saved["artists"]["deezer:42"] == {**artist, "scene": new, "scenes": [new, "Pop"]}
    assert (tmp_path / name).read_text() == marker
    assert response.json()["catalog"]["scene_filenames"][new] == "charts-dialekt.m3u"
    (tmp_path / name).rename(tmp_path / "charts-dialekt.m3u")
    again = client.post(url, json={"name": "charts-dialekt.m3u", "new_scene": "Swiss Pop"})
    assert again.status_code == 200
    assert again.json()["catalog"]["scene_filenames"]["Swiss Pop"] == "charts-swiss-pop.m3u"
    assert json.loads(state.read_text())["scene_files"] == {"charts-dialekt.m3u": "Swiss Pop"}


def test_unassigned_managed_scene_can_be_renamed_but_not_main_playlist(tmp_path, monkeypatch):
    _load_main(monkeypatch, tmp_path)
    monkeypatch.setattr(artist_scenes.config, "MUSIC_DIR", str(tmp_path))
    name = artist_scenes._legacy_scene_filename("Swiss German Mundart")
    content = "#EXTM3U\n#PLAYLIST:Mundart: Swiss German Mundart\n#CHARTS-STACK:MUNDART-SCENE\n"
    (tmp_path / name).write_text(content)
    (tmp_path / "mundart.m3u").write_text(content)
    (tmp_path / ".charts-artist-selection.json").write_text(json.dumps({
        **artist_scenes._default_selection(), "target_scene": "Swiss German Mundart",
    }))
    assert artist_scenes.list_scene_playlists()["playlists"][0]["renameable"]
    result = artist_scenes.rename_scene_playlist(name, "Dialekt")
    assert result["catalog"]["target_scene"] == "Dialekt"
    assert result["catalog"]["artists"] == []
    assert artist_scenes.list_scene_playlists()["playlists"][0]["renameable"]
    with pytest.raises(artist_scenes.ArtistSceneError):
        artist_scenes.rename_scene_playlist("mundart.m3u", "Andere")
    assert (tmp_path / "mundart.m3u").read_text() == content
