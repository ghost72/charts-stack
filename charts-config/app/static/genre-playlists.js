const genreEditor = {editing: "", dirty: false, saving: false, source: null, loading: false, sourceError: false};

function resetGenreEditor() {
  genreEditor.editing = "";
  genreEditor.dirty = false;
  const name = document.getElementById("genre-playlist-name");
  name.value = "";
  name.readOnly = false;
  document.getElementById("genre-select").value = "";
  document.getElementById("genre-limit").value = "50";
  document.getElementById("genre-save-status").textContent = "";
  renderGenreEditor(_artistSceneState);
  updateArtistSaveControls();
}

function updateGenreSaveControls() {
  const rule = _artistSceneState?.genre_playlists?.[genreEditor.editing];
  const unchanged = rule?.genre_id != null
    && document.getElementById("genre-select").value === String(rule.genre_id);
  document.getElementById("genre-save").disabled = genreEditor.saving
    || !_artistSceneState || (!genreEditor.source && !unchanged);
  document.getElementById("genre-cancel").hidden = !(genreEditor.editing || genreEditor.dirty);
}

function renderGenreEditor(data) {
  const select = document.getElementById("genre-select");
  let selected = select.value;
  let rule = data?.genre_playlists?.[genreEditor.editing];
  if (genreEditor.editing && !genreEditor.dirty && !genreEditor.saving) {
    if (rule?.genre_id != null) {
      selected = String(rule.genre_id);
      document.getElementById("genre-limit").value = String(rule.limit);
    } else {
      genreEditor.editing = "";
      rule = null;
      selected = "";
      document.getElementById("genre-playlist-name").value = "";
      document.getElementById("genre-playlist-name").readOnly = false;
    }
  }
  const status = document.getElementById("genre-catalog-status");
  status.textContent = t(genreEditor.loading || (!genreEditor.source && !genreEditor.sourceError)
    ? "genres.source_loading" : genreEditor.sourceError ? "genres.source_failed" : "genres.source_ready");
  status.className = genreEditor.sourceError ? "artist-section-hint error" : "artist-section-hint";
  const choices = [...(genreEditor.source || [])];
  if (rule?.genre_id != null && !choices.some(g => g.id === String(rule.genre_id))) {
    choices.push({id: String(rule.genre_id), name: rule.genre_name || String(rule.genre_id)});
  }
  choices.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  select.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = t("genres.choose_genre");
  select.append(placeholder);
  for (const genre of choices) {
    const option = document.createElement("option");
    option.value = genre.id;
    option.textContent = genre.name;
    select.append(option);
  }
  select.value = selected;
  select.disabled = !genreEditor.source;
  updateGenreSaveControls();
}

async function loadGenreSource() {
  if (genreEditor.loading || genreEditor.source) return;
  genreEditor.loading = true;
  genreEditor.sourceError = false;
  renderGenreEditor(_artistSceneState);
  try {
    const response = await fetch("/api/sources/deezer/genres", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok || !data?.ok || !Array.isArray(data.genres)) throw new Error("Invalid genre source");
    const genres = data.genres.filter(g => g && String(g.id) !== "0"
      && /^\d+$/.test(String(g.id)) && typeof g.name === "string" && g.name.trim())
      .map(g => ({id: String(g.id), name: g.name.trim()}));
    if (!genres.length) throw new Error("Empty genre source");
    genreEditor.source = genres;
  } catch (error) {
    genreEditor.sourceError = true;
  } finally {
    genreEditor.loading = false;
    renderGenreEditor(_artistSceneState);
  }
}

function editGenrePlaylist(name) {
  const status = document.getElementById("genre-save-status");
  if (genreEditor.dirty || genreEditor.saving || _artistSaving) {
    status.textContent = t("artists.playlist_save_first");
    status.className = "artist-msg error";
    return;
  }
  const rule = _artistSceneState?.genre_playlists?.[name];
  if (!rule || rule.genre_id == null) return;
  genreEditor.editing = name;
  const field = document.getElementById("genre-playlist-name");
  field.value = name;
  field.readOnly = true;
  renderGenreEditor(_artistSceneState);
  field.focus();
  field.scrollIntoView({block: "center"});
}

async function saveGenrePlaylist(event) {
  event.preventDefault();
  if (genreEditor.saving || _artistSaving) return;
  const status = document.getElementById("genre-save-status");
  const name = document.getElementById("genre-playlist-name").value.trim();
  const genreId = document.getElementById("genre-select").value;
  const limitValue = document.getElementById("genre-limit").value;
  const limit = Number(limitValue);
  const rule = _artistSceneState?.genre_playlists?.[genreEditor.editing];
  if (!name || !genreId || !/^\d+$/.test(limitValue) || !Number.isInteger(limit) || limit < 1 || limit > 100) {
    status.textContent = t("genres.choose_required");
    status.className = "artist-msg error";
    return;
  }
  if (!genreEditor.source && (!rule || genreId !== String(rule.genre_id))) {
    status.textContent = t("genres.source_failed");
    status.className = "artist-msg error";
    return;
  }
  if (!genreEditor.editing) {
    const filename = sceneFilenameKey(name, false);
    const owners = Object.keys(_artistSceneState?.scene_filenames || {});
    owners.push(...Object.keys(_artistSceneState?.genre_playlists || {}));
    const reserved = [...(_artistSceneState?.reserved_playlists || []),
      ...(_artistSceneState?.genre_catalog?.reserved_playlists || [])];
    if (owners.some(owner => sceneFilenameKey(owner, false) === filename
          || sceneFilenameKey(owner) === filename)
        || reserved.some(file => file.toLowerCase() === filename)) {
      status.textContent = t("genres.name_conflict");
      status.className = "artist-msg error";
      return;
    }
  }
  genreEditor.saving = true;
  updateArtistSaveControls();
  updateGenreSaveControls();
  status.textContent = t("genres.saving");
  status.className = "artist-msg";
  const pane = document.querySelector('[data-pane="artists"]');
  pane.inert = true;
  try {
    const response = await fetch("/api/genre-playlists", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name, genre_id: genreId, limit, replace: !!genreEditor.editing}),
    });
    const data = await response.json();
    if (!response.ok || !data.catalog) {
      const message = data.error === "genre_source_failed" ? t("artists.error.genre_source_failed")
        : data.error || `HTTP ${response.status}`;
      throw new Error(message);
    }
    genreEditor.dirty = false;
    genreEditor.editing = "";
    document.getElementById("genre-playlist-name").value = "";
    document.getElementById("genre-playlist-name").readOnly = false;
    document.getElementById("genre-select").value = "";
    document.getElementById("genre-limit").value = "50";
    renderArtistCatalog(data.catalog);
    const refreshed = await loadScenePlaylists();
    status.textContent = refreshed ? t("genres.saved")
      : `${t("genres.saved")} ${t("artists.playlist_refresh_error")}`;
    status.className = refreshed ? "artist-msg ok" : "artist-msg error";
  } catch (error) {
    status.textContent = `${t("genres.save_failed")} ${error.message || ""}`.trim();
    status.className = "artist-msg error";
  } finally {
    genreEditor.saving = false;
    pane.inert = false;
    updateGenreSaveControls();
    updateArtistSaveControls();
  }
}

function initGenrePlaylists() {
  document.getElementById("genre-playlist-form").addEventListener("submit", saveGenrePlaylist);
  document.getElementById("genre-cancel").addEventListener("click", resetGenreEditor);
  for (const id of ["genre-playlist-name", "genre-select", "genre-limit"]) {
    document.getElementById(id).addEventListener("input", () => {
      genreEditor.dirty = !!genreEditor.editing || !!document.getElementById("genre-playlist-name").value
        || !!document.getElementById("genre-select").value || document.getElementById("genre-limit").value !== "50";
      updateGenreSaveControls();
      updateArtistSaveControls();
    });
  }
}
