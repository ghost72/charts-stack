> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-config

Regular action buttons use the same blue accent across all tabs. Delete remains
red and restore green; tab navigation and audio controls retain their own styles.
Button sizing and behavior are unchanged.

Password-protected configuration web UI for the charts-stack (default port
`5058`). The source finder and Energy browser build snippets without changing
`.env` or `charts.json`. The artist panel manages a separate local Mundart
selection; the tag editor changes selected music files.

Sibling service of [`charts-dashboard`](../charts-dashboard): charts-config was
carved out of the dashboard. The source finder and the Energy-channel browser
that used to live in the dashboard now live here; the dashboard is a pure status
monitor.

---

## What it shows

The active tab opens with a short description of its purpose, followed by
the detailed usage notes.

The source finder and Energy browser are **read-only**. The artist panel and
tag editor have limited, password-protected write capabilities.

### Source finder (find new chart sources)

Helps you assemble the `charts-sync` `CHARTS` list – no more fishing IDs out of
web links by hand:

- **Deezer playlist search:** type a query → matching playlists with their ID
  and a **ready-made JSON snippet** to copy
  (`{"service":"deezer","type":"playlist","source":"<id>",…}`).
- **Deezer artist search:** type an artist name (e.g. `Gölä`, `Polo Hofer`) →
  matching artists with their ID and fan count (helps tell apart artists with
  the same name) plus a **ready-made snippet** for the artist's top tracks
  (`{"service":"deezer","type":"artist","source":"<id>",…}`).
- **Deezer genre table:** the stable genre IDs as a list (cached once per day,
  refreshed on the first call after expiry – see `DASHBOARD_GENRE_CACHE_TTL`).
  If the live call fails, a built-in static fallback list is used.
- **Last.fm tag check:** enter a tag (e.g. `country`) → validates via
  `tag.getTopTracks` whether it is usable and shows the matching snippet
  (`{"service":"lastfm","method":"tag.getTopTracks","tag":"<tag>",…}`). This part
  is **only active with `LASTFM_API_KEY`**; without a key it is greyed out.
- **Apple Music country charts:** choose CH, DE, FR, GB or US and a limit of
  1–100 → validate the public most-played feed, preview up to ten tracks and
  copy a validated `service:"apple"` snippet. No account or API key needed.
  Checking happens only on submission; it neither changes configuration nor
  enables monitoring.

Every snippet can be copied and pasted straight into the `CHARTS` variable of
your `.env`/`compose.yml` (or `charts.json`). Full reference:
[`CHARTS-SOURCES.en.md`](../CHARTS-SOURCES.en.md).

### Energy channel browser (NRJ)

A second, equally **read-only** panel: a list of the confirmed Energy (NRJ)
webradio channels, so you do not have to hunt slugs in the README when assembling
`ENERGY_CHANNELS`. Per channel it shows the display name, slug, genre, a **status
badge** (active + the running mode, or inactive) and a **ready-made JSON snippet**
for the `ENERGY_CHANNELS` array. The channel browser itself is read-only.
Paste the snippet into the existing list in the separate `.env` tab, unless an
external channel file takes precedence. After saving, recreate the affected
containers yourself; the tab displays, but does not execute, the command.

The status comes from the running `charts-energy` config (which the service
writes into its status JSON, read read-only by charts-config); a missing/old
status file simply shows every channel as "inactive" (fail-open). Each default
mode needs one extra wiring step:

- **own** → append its `missing-energy-<name>.txt` (shown in the snippet) to
  `MISSING_FILES` so `charts-download` fetches its missing songs.
- **feed** → add `{"service":"energy","theme":"<theme>"}` to that theme's
  playlist in `charts-sync/charts.json` (theme byte-for-byte, e.g. `mundart`).
- **library** → append the shared `missing-energy-library.txt` to `MISSING_FILES`
  (all `library` channels share this one file).

Mode details: [`charts-energy/README.en.md`](../charts-energy/README.en.md).

### Artists & scenes

For a language or regional playlist, define it with suitable sources in
`CHARTS` or `CHARTS_FILE`, for example with `name: chanson.m3u`. In the `.env`
tab, set `CHARTS_ARTIST_PLAYLIST=chanson.m3u`, save, and recreate `charts-sync`
and `charts-config` with the displayed command. **Artists & Scenes** displays
the selected filename. After the next sync, assign artists to your own scenes
and select a target scene. The default remains `mundart.m3u`. Apple country
charts do not detect language or dialect.

The password-protected artist panel reads the inventory that `charts-sync`
updates with main and featured artists from all active charts-sync
feeds. Each entry shows its feed sources and track counts. Artists supplied by
Energy as names only remain unverified until a Deezer artist ID is resolved and
linked through the search above the list. The action in a table row focuses
the search and fills in the name. Names are never merged by fuzzy matching.
**Preview** loads available clips from the first five Deezer top tracks to
help confirm the identity. When several clips are available, **↻** switches
to the next track by the same artist. Audio plays directly from Deezer; a
Deezer link remains if no preview is available.
Switching while a preview is playing starts the next one automatically;
switching while paused keeps playback paused.

Scene assignment and activation are **human choices**, not automatic language
or dialect detection. An optional evidence note can be stored. Manually added
Deezer artists can contribute their Deezer top 50 to the filter or additional
scene playlists. Filtering starts disabled and only takes effect when
explicitly enabled; then
`mundart.m3u` keeps only enabled, linked artists assigned to the selected
scene. Under **Scene for the main playlist filter**, enter the same scene name
as in the enabled artists' **Scene** fields. The configured sources and
playlist name stay in place; the filter applies to tracks for the main M3U
and its download queue. Other playlists are unchanged.

A note under **Playlist settings** explains the difference in the interface.
**Scene for the main playlist filter** is not the playlist title. New artist
state starts with the neutral `My artists` value; existing saved names such as
`Swiss German Mundart` remain unchanged. Choose the artist group that the
optional main filter should use.
Artist/scene management is general-purpose: suggestions and scene matches
come from **all configured charts-sync sources**, not just the main playlist.
Scenes can be named `Mundart-Pop`, `French Rock`, or `Jazz`. The display title
is the scene name without a mandatory `Mundart:` prefix. New files use names
such as `charts-french-rock.m3u`, without a hash. The UI and write API reject
filename collisions (for example `Rock & Pop` and `Rock-Pop`).
Existing managed `mundart-szene-…m3u` files also migrate to
`charts-<scene-name>.m3u` on the next sync. Music files are untouched.
Navidrome may import the renamed M3U as a new playlist; the previous playlist
identity is not guaranteed. The main playlist and filter remain independent.
Genre playlists from file tags are available independently (see below).
Internal `/api/mundart/` routes and the old managed-M3U marker remain for
compatibility; they do not constrain scene names or language.
**Create an additional M3U for each scene** is independent of the main filter and off by default.
Enable it and save: on the next `charts-sync` run each scene with approved
artists gets a separate playlist of matched library tracks. The target scene
for `mundart.m3u` is unchanged.
Under **Automatically download missing top tracks**, opt in one scene at a time
and choose the Deezer limit per artist (1–100, initially 50). This applies to
manually added, enabled and linked artists. After you save, searching,
importing and updating the playlist run on the usual service schedules.
`charts-download` searches slskd like it does for regular charts, without a
manual provider list. Normal quality filters, blocks, limits and `DRY_RUN`
still apply. Only download music you are entitled to download; Deezer does
not supply the audio files.
Enter one scene per line to assign an artist to several scenes.
Editing scenes or enablement mirrors the change across rows with the same
Deezer ID. Existing single-scene assignments are preserved.
A scene name alone does not unlock enablement for an unverified row. Link
the matching identity first; a joint credit is not automatically equivalent
to one of its artists.
Linking, adding, or switching between sections in charts-config preserves
unsaved edits in the open browser tab. After **Add**, the table jumps to the
matching artist row. **Save changes** is available in Playlist settings and
Artist assignments; both buttons save all edits in Artists & scenes.
The browser warns about unsaved changes when leaving, closing, or reloading
the page. Switching sections within charts-config keeps the draft.
Write requests for artists, scenes, and the `.env` editor require an
authenticated session and `Content-Type: application/json`.
The table scrolls independently. Column headings and **Save changes** stay
accessible while you edit a long list.
The artist count and column headings sit outside the vertical scrolling area.
Under **Scene playlists**, **Delete permanently** removes the scene's artist
assignments and download or genre rule. Artists and Deezer IDs remain;
artists without another scene are disabled. Other scenes, music files and
the main playlist stay unchanged. The active main-filter scene is protected.
A remaining managed M3U is removed on the next sync. The temporary deletion
request then disappears: no permanent block remains and the name can be reused.
Old blocked entries without an M3U disappear immediately after confirmation.
Unmanaged files and symlinks are never deleted. Save or discard drafts first;
the dark dialog's **Cancel** or Escape makes no changes.

### Deezer genre playlists

Form controls are at least 44px high with clear field-group spacing. Genre
and track limit sit side by side on desktop and stack on narrow screens.

Enter a name, choose one official Deezer genre and set a per-playlist track
limit (1–100, suggested **50**). Genres come from Deezer's `/genre` API, not
local music tags. Catalog errors are explicit; no static or local fallback list
is offered. Saved genres remain visible, and their limits can be edited without
another catalog lookup. Changing the genre requires Deezer confirmation.

The next sync fetches `/chart/<genre-id>/tracks` up to the selected limit.
Deezer may supply fewer tracks; the limit is not a promised playlist size.
Only chart-listed titles are matched against the library, regardless of their
local genre tags. Paths are deduplicated; music tags are never changed.
Missing titles enter the existing `.charts-scene-missing.txt` queue with the
playlist name as provenance. The usual slskd download/import pipeline, quality
filters, global artist exclusions, quotas and `DRY_RUN` apply.
Deezer supplies metadata, not audio. After an authorized download and import,
the next sync fills the M3U. Empty/failed Deezer reads preserve existing M3Us,
do not enqueue new downloads for that playlist and surface an explicit error.

Rules are saved independently of artist edits and apply on the next sync
without an additional trigger. Edit changes genre/limit; rename and permanent
delete work in the overview, including before the first M3U exists.
Files use hashless `charts-<name>.m3u` in the same collision-protected namespace
as scenes. Genre rules do not depend on the main filter or extra-scene switch.
Legacy file-tag rules are not silently converted/deleted: existing entries
remain renameable/deletable, but the local genre picker is removed.

### Renaming scenes

**Rename scene** moves artist assignments, the top-track limit, and the main
filter scene if applicable. Deezer IDs stay the same; the next sync updates
both the title and M3U filename to follow the new scene name. Save pending edits first. Renaming does not
merge existing scenes.
The rename dialog uses the dark UI theme, including on mobile screens.
Enter confirms; Escape or **Cancel** closes it without changes.
Empty names are rejected. Managed older scene M3Us without remaining artist
assignments can also be renamed.
**Refresh list** rereads existing files and shows the refresh time.
It does not start a sync or generate an M3U.

The inventory and selection are stored separately from `.env` and
`charts.json` in the shared Charts directory:

- `.charts-artist-inventory.json` — feed candidates, sources, and timestamps
  updated by `charts-sync`.
- `.charts-artist-selection.json` — local scenes, Deezer identity links, and
  enablement, atomically saved by charts-config.

If the selection file is missing or empty, the existing Mundart feed continues
unchanged; newly discovered artists are never approved automatically.

### Manual tag editor (read-write)

**Show / refresh missing metadata** displays the latest import-backfill
snapshot for matching manifest MP3s. Select a readable entry, fill year, album
or track number and save. Listed files are reread on refresh; newly incomplete
files appear only after the next import backfill. Unavailable files and
unreadable tags are identified separately. The list scrolls within a bounded
area even when there are many entries. The summary counts missing years,
albums and track numbers separately. An existing album value is considered
populated even when it matches the song title; it is neither reported as
missing nor automatically replaced.
Use **Filter by** to narrow the list to year, album, track number or unreadable
tags; the summary continues to count all cases.

The tag editor is another **write** tool on the page. Search the music library,
pick a song and edit its tags (artist, title, album, year, track) and/or replace
the embedded cover (upload or image URL), then **Save**. The tag edit is always written
**in place first** in the existing `.mp3` (fail-open: the edit is never lost even
if the move below fails), the file's modification time is **preserved** (so
Navidrome/audiobookshelf-style "recently added" floods are avoided), and only the
fields you actually changed are written.

On every successful save the two stack provenance markers (`TXXX:CHARTS_SOURCE`
and `TXXX:CHARTS_ALBUM_SRC`) are **cleared**, so the automatic
backfill/album-recorrection treats the song as hand-curated and never overwrites
your edit again. The editor **never empties a field** – leaving the year or track
box blank keeps the existing value (an emptied field on a manifest song would be
refilled by the backfill). A small badge marks each hit as **stack** (downloaded
by the stack, via the marker or the manifest) or **curated** (your own file).

**Folder follows the artist tag (automatic move).** When a save *changes the
artist*, the file is moved on disk so its folder matches the corrected artist —
with a strict **one-folder guarantee**:

- An existing artist folder with the **same name** or the **same folded key**
  (casing, hyphen, apostrophe or accent variants like `Motörhead`/`Motorhead`,
  `rod stewart`/`Rod Stewart`, `DJ.ILHAM`/`DJ ILHAM`) → the file is **merged
  into** that one folder automatically, no second folder is ever created.
- If the old artist folder **empties out** after the file leaves and there is no
  matching folder → the old folder is **renamed** to the new artist name (one
  folder, carrying everything with it).
- A **truly new** artist with no match → a new folder is created automatically.
- A **decision dialog** appears only for two genuinely ambiguous cases, and
  **nothing is moved until you choose**:
  - a *stylized near-spelling* (e.g. `P!nk`) that matches an existing `Pink/`
    folder which still holds **other songs** → *file into `Pink/`* /
    *create new `P!nk/`* / *cancel*;
  - a **song collision** — the destination already has this song (exact file or
    the same title under a Lidarr `NN - Title` name) → *keep existing, don't move
    mine* / *cancel*. The editor **never overwrites** and never writes a `(2)`.
- Cancelling a dialog keeps the **saved tags at the old path** — the tag edit is
  already committed, only the move is skipped.

Moves are **stack-singles only** (`<Artist>/<Title>/<Title>.mp3`); Lidarr
`<Album> (Year)/NN - Title` albums and curated/`Various Artists` compilations are
left **in place**. The move is a pure `os.rename` (mtime preserved, no copy
fallback: a cross-device layout is hard-refused and the file stays put with its
saved tags). The one-folder durability **depends on Lidarr "Rename Tracks" being
OFF** — otherwise a later Lidarr scan can rename the moved single into an
`<Album> (Year)/` folder and reintroduce a duplicate.

Notes and limits:

- **MP3 only.** Non-MP3 files can be selected and viewed but are shown
  read-only (saving is restricted to `.mp3`).
- **Cover replace only** (no removal); a cover URL must be `http`/`https`
  (redirects are not followed – a small SSRF guard).
- This section needs the library mounted **read-write** at `/music/Music` (see
  Installation). Without that mount the editor is inert: the search returns
  nothing and nothing is writable. **`/charts` stays read-only** – the manifest
  is only read there.

**File + bitrate (read-only).** Below the Track field a read-only row shows the
song's **library-relative path** and its **bitrate/format** (`320 kbps`, or
`FLAC (lossless)` for lossless formats). Helps spot leftover non-MP3 (FLAC)
files. Purely informational – never saved.

**In-browser play + download (optional, default OFF).** When the
`TAGEDITOR_AUDIO_ENABLED` flag is on, the loaded song's cover shows a **play
overlay** (plays the file through an HTML5 `<audio>` element, with Range/seek
support) and, below it, a **download button**. Play works for **MP3 only** – for
FLAC/lossless the play button is disabled (hint "Only MP3 can be played here")
because browsers (especially Safari) often cannot decode them; **download works
for all formats** (including FLAC).

> **Security note (important).** The audio/download endpoint
> (`GET /api/tageditor/audio`) is triple-gated: **flag-gated**
> (`TAGEDITOR_AUDIO_ENABLED`, **default OFF** – a fresh clone has no audio egress
> at all, fail-closed), **auth-gated** (the same login session as every other
> `/api/tageditor/*` route – `401` when unauthenticated) **and** **path-guarded**
> (the same `MUSIC_DIR` path guard as save – a traversal/outside path is refused,
> no arbitrary file read). The flag is an **additional** gate, not a replacement
> for the login or the path guard. Turn it on **only for private/authenticated**
> use. If charts-config is exposed to the internet, use a **strong password** and
> ideally put it behind a **VPN/reverse-proxy auth**. This is a private tool for
> your own library, **not a public music server**.

**Convert to MP3 (per non-MP3 song).** When a non-MP3 audio file is loaded
(FLAC/M4A/OGG/…), a **Convert to MP3** button appears in the actions row. After
a confirm step (honest warning: **FLAC→MP3 is a quality downgrade**, lossless →
lossy, not reversible), the service transcodes the file to **MP3 320 kbps CBR**
via ffmpeg, carries the tags + embedded cover over, preserves the mtime ("date
added"), and writes the result as `<name>.mp3` in the same folder. The
**original is NOT deleted** but moved, reversibly, into the trash bin
`/music/Music/.charts-trash/flac2mp3-<date>/` (same convention as the dedup).
The provenance markers are cleared → the song becomes **hand-managed** (the
automation never touches it again). If ffmpeg fails, the original is left
untouched and nothing is moved to trash. The convert button is **auth-gated
only** (like every `/api/tageditor/*` route), **not** behind
`TAGEDITOR_AUDIO_ENABLED` – that flag gates only the play/download endpoint. The
deliberate per-song click does not violate the "automation never touches the
hand-added library" rule.

**Delete song (red button, when a song is loaded).** A **red** **Delete song**
button sits at the right of the editor's actions row (the same red Trash-Manager
treatment). It only appears while a song is loaded and works for **any** audio
format the editor can load (not MP3-only). After a confirm step it does **both**:
(1) the audio file is moved **reversibly** into the trash bin
`/music/Music/.charts-trash/tageditor-delete-<date>/` (same convention as
convert/dedup – **not** hard-deleted, mtime preserved; restorable from the
trash), and (2) the song's **manifest entries** are removed (key = file
basename, lowercased; for YouTube/Upload songs the download name
`<Artist> - <Title>.mp3`, found by matching artist + title – every matching
entry) and the matching **source files in the Charts folder** are moved into the
song's trash entry (hidden `.charts-sources/` subfolder, not listed as its own
row, removed together with the entry), so **charts-import does not re-import it
on its next run** (manifest entries are persistent – without this step the song
would come back). Restore puts the library file, the source files and all
manifest entries back (nothing is overwritten). Songs deleted with the OLD
version have no source sidecar and may need a manual clean-up (remove the
manifest entry and the file in the Charts folder). This needs `/charts` mounted
read-write in the container (`compose.yml`). If the
song sits in its own stack-single `<Artist>/<Title>/` folder and that folder
becomes empty, the empty folder is cleaned up; a folder that still holds other
files is left intact. The editor then returns to the empty "pick a song" state.
**Use case:** a YouTube download landed with a wrong artist name and was
re-downloaded with the correct name – this button removes the resulting
duplicate in one click. The delete endpoint (`POST /api/tageditor/delete`) is
behind the same auth gate and the same `MUSIC_DIR` path guard (realpath, no
traversal/outside path) as every other `/api/tageditor/*` route; it is **not**
behind `TAGEDITOR_AUDIO_ENABLED`.

**Non-MP3 list (below the editor).** A read-only list shows **only non-MP3 audio
files** in the library (FLAC/M4A/OGG/… – no txt/cover.jpg/.m3u/.nfo). Trash,
`@eaDir`, `#recycle` and dot-folders are skipped (same index pruning as search).
Each entry shows the library-relative path + the format and offers **Open in tag
editor** to load the song above (e.g. to convert it to MP3).

**Trash manager (below the non-MP3 list).** The whole
`/music/Music/.charts-trash/` tree is listed as a **sortable five-column table**
– **PATH · ORIGIN · DATE · SIZE · ACTION** – plus – at the top – the **total
size** the trash occupies and the **file count**:

- **PATH** shows only the library-relative path **without** the internal source
  subfolder prefix: `dedup-2026-11-02/When in Rome/The Promise/The Promise.mp3`
  displays as `When in Rome/The Promise/The Promise.mp3` (still in the neutral
  label chip, wrapping inside its column). This is **display-only** –
  delete/restore still operate on the real full trash path.
- **ORIGIN** is a small badge derived from the prefix: `dedup`, `Tag Editor`
  (tageditor-delete), `FLAC→MP3` (flac2mp3), `Upgrade` (the bare `<YYYYMMDD>/`
  entries), or `—` when the prefix does not parse.
- **DATE** is the date parsed out of the prefix (incl. time for Tag-Editor
  deletes, e.g. `2026-11-05 16:26:44`) in its own column.
- **SIZE** is the file size; **ACTION** holds the buttons (Delete / Restore on
  restorable rows only).

**Clicking a column header** sorts by that column and toggles asc/desc (▲/▼
indicator): PATH/ORIGIN alphabetically, DATE chronologically (by the real
timestamp; unparseable entries sink to the bottom), SIZE numerically (by bytes).
Default sort: **DATE descending** (newest first). **All** files are listed (also
non-audio sidecars like `cover.jpg` that were moved alongside). The list is
read-only and TTL-cached. Three actions:

- **Permanently delete a single file** (hard delete) – with a per-file confirm.
- **Empty the whole trash** – with a prominent confirm. This is
  **irreversible**: the trash IS the last reversible stage, so deleting here is
  final.
- **Restore a single song (green button)** – shown **only** on rows that were
  deleted via the Tag-Editor's "Delete song" button (under
  `tageditor-delete-<date>/`); convert/dedup and charts-import upgrade/replace
  rows keep only the red delete button (their original location is ambiguous, so
  they are out of scope). After a light confirm (restore is non-destructive) the
  file is moved back to its **exact original library location**, its charts-import
  **manifest entry is re-created** (so the stack recognizes the song again), and
  a pruned `<Artist>/<Title>/` folder is recreated. **Collision = refuse:** if a
  file already exists at the original location (e.g. the song was re-downloaded
  under the correct name) the restore is refused and the existing file is **never
  overwritten**. This works because the Tag-Editor **delete** writes a tiny
  restore sidecar (`<trashed-file>.trashinfo.json`, hidden from the list)
  recording the original path + removed manifest entry. **Only songs deleted with
  the Tag-Editor AFTER this feature shipped are restorable** – older deletions
  have no sidecar and the button refuses with a clear "no restore data" message.

**Security:** both the list **and** the delete/empty/restore actions sit behind the same
auth gate as every other `/api/tageditor/*` route (401 anonymous). Deletion is
**strictly confined to `.charts-trash`**: on top of the existing MUSIC_DIR
guard, an additional realpath-containment check requires the resolved path to
sit **inside** the trash, so **no** library file outside the trash can ever be
deleted through this endpoint (defense against traversal AND against a bug
passing a non-trash path). **Symlinks are never followed out of the trash**, and
emptying requires an **explicit confirm flag** server-side (not a single click).
**Restore reuses the same guards** (the source must resolve strictly inside
`.charts-trash`; the restore target is guarded inside `MUSIC_DIR` and may never
land back in the trash), so it can neither read from outside the trash nor write
outside the library.

> **Why this manual visibility/control?** The automatic retention cleanup
> (`TRASH_RETENTION_DAYS`, default 30, in **charts-import**) deletes trashed
> files **by file mtime**, and files are moved into the trash with `os.rename`,
> which keeps their **original (often old) mtime**. So a file that was already
> old when it was trashed can already be ">30 days" and may be purged on the
> **next** charts-import cleanup run – not 30 days after the move. That is why
> the manager shows the sizes and lets you review/delete deliberately rather
> than relying on the age-based sweep.

> **ffmpeg** is now **part of the charts-config image** (required by the MP3
> 320 CBR convert action), installed via `apt-get`. It has no other role beyond
> the audio/download endpoint.

## Where the data comes from

- **Deezer / Last.fm / Apple Music:** direct query of the public HTTP APIs (WAN). Without
  `LASTFM_API_KEY` only the Last.fm part is greyed out – the rest works.
- **Energy live status:** from the status JSON that `charts-energy` writes into
  `Charts/.charts-status/` – mounted **read-only** by charts-config.
- **No Docker access, no restart button:** unlike the dashboard, charts-config
  does **not** need the Docker SDK. The artist panel only writes the local
  selection file in the Charts directory; `.env` and `charts.json` remain
  untouched. No socket mount, proxy, or container start/stop.

## Login

charts-config has its **own** login, separate from the dashboard/songrequest (it
shows internal snippets):

- `CONFIG_USERNAME` (default `admin`) + `CONFIG_PASSWORD`.
- If `CONFIG_PASSWORD` is empty, it falls back to the shared
  `APP_USERS`/`APP_PASSWORD`.

Without any password the service refuses to start (fail-closed) – the page may be
exposed.

## Configuration (central `.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `CONFIG_PORT` | `5058` | host port of the web UI |
| `CONFIG_USERNAME` | `admin` | charts-config username |
| `CONFIG_PASSWORD` | – | password (empty = fall back to `APP_USERS`/`APP_PASSWORD`; if both are missing the service does not start) |
| `SESSION_SECRET` | – | random string for session cookies; empty = re-login after restart |
| `LASTFM_API_KEY` | – | optional, for the Last.fm tag check (empty = Last.fm box greyed out) |
| `DASHBOARD_GENRE_CACHE_TTL` | `86400` | cache TTL of the Deezer genre table in the source finder (seconds; refreshed on the first call after expiry) |
| `CHARTS_LANG` | – (empty = English) | language for the UI **and** logs (`de`/`fr` available) – one stack-wide switch, no in-UI toggle |
| `CONFIG_DONATE` | `1` | discreet donate footer (PayPal/Revolut); `0`/`false`/`no` hides it |
| `TAGEDITOR_INDEX_TTL` | `300` | tag-editor search-index rebuild cadence (seconds); supplied by compose |
| `TAGEDITOR_COVER_MAX_BYTES` | `8388608` | max cover upload/fetch size (8 MiB); supplied by compose |
| `TAGEDITOR_COVER_FETCH_TIMEOUT` | `15` | cover URL fetch timeout (seconds); supplied by compose |
| `TAGEDITOR_AUDIO_ENABLED` | `0` | in-browser play + download of a loaded song (**default OFF**, fail-closed). When on, the audio endpoint is additionally behind the login **and** the path guard; turn on only for private/authenticated use (see the security note) |
| `TAGEDITOR_CONVERT_TIMEOUT` | `600` | timeout (seconds) for a single FLAC→MP3 ffmpeg transcode; bounds a hung ffmpeg |

### The .env tab

Multiline values such as `CHARTS` use the full group width and a taller,
vertically resizable editor. Displaying a value does not automatically
reformat or change JSON. A successfully loaded `CHARTS_FILE` takes priority
over `CHARTS`; clear the file-path field and save to use the UI's `CHARTS`
value instead.

The authenticated tab is last in the navigation. It groups settings using
`.env.example`, uses its comments as help text, and marks links to the
relevant service documentation with 📖. Saving rebuilds
the central `.env` from the current template, carries forward saved values, and
preserves additional active variables at the end. Comments and formatting from
the old `.env` are replaced by the current template.
Unchanged values retain their original Compose syntax (including `$VAR` and
`${VAR}` expressions); newly entered or edited values are stored literally.
Custom variables are active keys missing from the mounted template. They are
not necessarily errors and are not deleted.

Secret values are never returned to or displayed in the browser. Leaving a
password field blank keeps its current value; it can be explicitly replaced or
cleared. Before an actual file change, a private backup is created in
`.env-backups/`. `charts-config` stays unprivileged; `.env` and the separate
backup directory are read-write mounts for this feature, while `.env.example`
is read-only. The configured `PUID:PGID` must be able to write the host `.env`
and `.env-backups/`.

Saving does **not** restart containers. Directly below the save confirmation
and again at the bottom of the tab, the editor shows a copyable
`docker compose up -d --no-deps --force-recreate ...` command for the affected
services; run it from the project directory. No image rebuild is required.

## Access

On the LAN directly via `http://<host>:5058`. For external access put it behind a
reverse proxy (e.g. `config.your-domain.tld` → `http://<host>:5058`, no WebSocket
needed) and use **HTTPS** – the login uses a session cookie.

## Security

- Runs as an **unprivileged user** (`PUID:PGID`), **not** as root.
- **No** Docker access: the source finder talks to Deezer/Last.fm/Apple Music (WAN) and
  reads the charts-energy status JSON. No socket mount, no proxy, no
  container start/stop.
- Mounts the Charts folder read-write for the local artist selection and
  tag-editor metadata. `charts.json` remains untouched.
- The authenticated `.env` tab has a read-write mount for the fixed `.env`
  file, a read-only mount for `.env.example`, and a separate `.env-backups/`
  mount. The project directory and Docker socket are not mounted; the editor
  can modify only the fixed `.env` path.

## Installation

charts-config is part of the charts-stack and is **not** installed on its own,
but via the shared stack project (one `compose.yml` + one central `.env` in the
`charts-stack` folder). See the main README for full installation. To
rebuild/restart just this service: `docker compose up -d --build charts-config`
from the `charts-stack` folder.

### Artist/scene panel – deployment

The artist inventory is produced by `charts-sync`, while charts-config serves
and updates the selection. Rebuild and recreate both services after deploying
this feature:

```bash
docker compose up -d --build charts-sync charts-config
```

The inventory and selection files are created in the shared Charts directory;
there is no data migration. Filtering remains disabled until explicitly enabled
in the artist panel.

### Manual tag editor – mount and rebuild

The tag editor needs the music library mounted **read-write**. The stack
`compose.yml` already adds this to the `charts-config` service:

```yaml
    volumes:
      - ${MUSIC_ROOT:-/volume2/music}/Charts:/charts             # status + manifest + local selection
      - ./lang:/lang:ro                                          # language files
      - ${MUSIC_ROOT:-/volume2/music}/Music:/music/Music         # read-write for the tag editor
```

`/charts` is read-write (including manifest maintenance and the local artist
selection); `/music/Music` stays read-write for the tag editor. After pulling
the new code, rebuild the image so
the new dependencies (`mutagen`, `Pillow`) are installed:

```bash
docker compose build --no-cache charts-config
docker compose up -d charts-config
```

**New language keys need a restart.** The `lang/{en,de,fr}.json` files are
bind-mounted read-only at `/lang`; a `--no-cache` rebuild alone does **not**
pick up changed language files. After copying the updated `lang/` files into the
NAS mount, run `docker restart charts-config` so the loader re-reads the table –
otherwise new labels can show as raw keys. On Synology, if raw keys
persist, re-create the container (`docker compose up -d --force-recreate
charts-config`).

## Language

charts-config follows the stack-wide `CHARTS_LANG` (empty = English; `de`/`fr`
available). One switch for the whole stack, no in-UI toggle – change the language
by editing the `.env` and restarting the container (no rebuild; the language
files come read-only from the `/lang` mount).
