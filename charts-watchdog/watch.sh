#!/bin/sh
# charts-watchdog - ueberwacht ALLE Container des charts-stack und meldet per
# Pushover, wenn einer in einen schlechten Zustand wechselt (und wenn er sich
# wieder erholt). Reines Shell-Skript, kein Build.
#
# Was gemeldet wird (pro Container):
#   - unhealthy      : Healthcheck schlaegt an (aktuell nur slskd hat einen;
#                      kuenftige Healthchecks werden automatisch mitueberwacht).
#   - restarting     : Container haengt in einer Neustart-Schleife (echtes
#                      Dauerproblem - restart:unless-stopped kommt nicht hoch).
#   - exited/dead    : Container ist (unerwartet) gestoppt und bleibt es.
#   - Erholung       : zurueck auf running+healthy -> Entwarnung.
#
# Anti-Spam / Anti-Flapping (wichtig!): Pro Problem-Episode gibt es GENAU EINE
# Problem-Meldung und GENAU EINE Entwarnung. Konkret:
#   - Problem wird erst nach DEBOUNCE anhaltenden schlechten Polls gemeldet.
#   - Nach einer Problem-Meldung gilt ein COOLDOWN: in dem Fenster keine zweite
#     Problem-Meldung fuer dieselbe App - egal wie oft sie flippert.
#   - Entwarnung kommt erst, wenn die App RECOVER_POLLS am Stueck ok ist. Eine
#     flippernde App (ok/problem/ok/...) erreicht das nie -> keine Entwarnung
#     und damit auch keine erneute Problem-Meldung. Du bekommst also NICHT
#     dauernd Nachrichten, wenn eine App instabil ist.
#
# Was NICHT gemeldet werden kann: ein Container, der LAEUFT, dessen interne
# Logik aber haengt. Das wuerde pro Dienst einen eigenen Healthcheck brauchen.
# Fuer die Scheduler-Dienste (laufen in einer Schleife) ist der Lauf-/Health-
# Zustand das aussagekraeftige Signal.
#
# Laeuft in einem docker:cli-Container mit read-only Docker-Socket und pollt
# den Zustand ueber die lokale Docker-Engine.

set -eu

# Zu ueberwachende Container (Leerzeichen-getrennt). Default: der ganze Stack.
# Ueber WATCH_CONTAINERS in der .env/compose anpassbar.
CONTAINERS="${WATCH_CONTAINERS:-slskd charts-sync charts-hitparade charts-flashback charts-download charts-import charts-recommend charts-upgrade charts-songrequest charts-digest}"
INTERVAL="${WATCH_INTERVAL_SECONDS:-30}"

PUSHOVER_TOKEN="${PUSHOVER_TOKEN:-}"
PUSHOVER_USER="${PUSHOVER_USER:-}"
PUSHOVER_DEVICE="${PUSHOVER_DEVICE:-}"
# Pushover-Benachrichtigungston. Leer = Account-Default (kein sound-Feld im
# Request). Default fuer charts-watchdog: "siren".
PUSHOVER_SOUND="${PUSHOVER_SOUND:-siren}"
# Cache-Dauer (Sekunden) fuer die vom Pushover-Account abgerufene Geraeteliste
# (siehe _valid_pushover_devices). Dagegen wird PUSHOVER_DEVICE vor dem Senden
# gefiltert, damit ein ungueltiger/deaktivierter Geraetename nicht Pushovers
# "an alle"-Fallback ausloest (Doppel-Push). Standard 3600s = 1h.
PUSHOVER_DEVICES_TTL="${PUSHOVER_DEVICES_TTL:-3600}"
# Pushover fuer diesen Dienst global stummschalten (0 = aus). Default: an.
PUSHOVER_ENABLED="${PUSHOVER_ENABLED:-1}"

# --- Externe/abhaengige Dienste --------------------------------------------
# Erreichbarkeits-Checks fuer die Dienste, von denen der charts-stack abhaengt
# (slskd, Lidarr, ListenBrainz, Deezer, Last.fm, iTunes, Pushover). Kriterien
# 1:1 wie im charts-dashboard (collector.py _slskd_status/_lidarr_status/
# _external_status). Lidarr + Last.fm werden nur ueberwacht, wenn konfiguriert.
SLSKD_HOST="${SLSKD_HOST:-http://slskd:5030}"
SLSKD_API_KEY="${SLSKD_API_KEY:-}"
LIDARR_URL="${LIDARR_URL:-}"
LIDARR_API_KEY="${LIDARR_API_KEY:-}"
LASTFM_API_KEY="${LASTFM_API_KEY:-}"

# --- i18n (uebersetzbare Ausgabetexte, geteilter lang/-Satz) ----------------
# charts-watchdog ist der einzige nicht-Python-Dienst; t() spiegelt die Semantik
# des Python-Loaders (Pfadsuche, gewaehlte Sprache -> en -> Key-Name, nie
# crashen), bleibt aber BusyBox-tauglich (nur grep/sed/printf). Die
# watchdog.*-Keys nutzen positionale %s + printf (NICHT benannte {name}).
#
# EINMALIGE Normalisierung beim Skriptstart (set -u-sicher): CHARTS_LANG und
# LANGDIR werden hier top-level in Variablen festgelegt und danach pro t()-Aufruf
# nur noch gelesen - kein bares $CHARTS_LANG, kein tr je Aufruf. MUSS vor dem
# ersten t()-Nutzer laufen (Start-Banner liegt in main_loop(), also sicher nach
# dieser Zeile).
CHARTS_LANG="$(printf '%s' "${CHARTS_LANG:-}" | tr '[:upper:]' '[:lower:]' 2>/dev/null || true)"
# Sprachverzeichnis in Python-Prioritaet: $CHARTS_LANG_DIR -> /lang -> $0-relativ
# (../lang, ./lang). Erster existierender Treffer gewinnt; Default /lang (t()
# degradiert dann sauber zum Key-Namen). $0-Kandidaten sind nur fuer den Dev-/
# Standalone-Fall relevant (im Container ist $0=/watch.sh -> ../lang=./lang=/lang).
LANGDIR="/lang"
_wd_scriptdir="$(dirname "$0" 2>/dev/null || echo .)"
for _wd_cand in "${CHARTS_LANG_DIR:-}" "/lang" "${_wd_scriptdir}/../lang" "${_wd_scriptdir}/lang"; do
  [ -n "$_wd_cand" ] || continue
  if [ -d "$_wd_cand" ]; then
    LANGDIR="$_wd_cand"
    break
  fi
done

# _lookup KEY LANG -> Template zu KEY aus LANG.json, sonst leer. Jeder Schritt
# mit `|| true` (set -eu-sicher). Zwei-Schritt grep->sed:
#   Schritt 1: die EINE Key-Zeile per grep -F "<key>": isolieren (Begrenzer mit
#     schliessendem Quote UND Doppelpunkt gegen Prefix-Kollision, z.B.
#     watchdog.notify.problem vs. ...problem_title) + head -n1 (Ein-Zeilen-Vertrag).
#   Schritt 2: den Value zwischen den Value-Quotes greedy ausschneiden (Anker auf
#     dem letzten " der Zeile; optionales Trailing-Komma erlaubt).
_lookup() {
  _lf="$LANGDIR/$2.json"
  [ -f "$_lf" ] || { echo ""; return 0; }
  _line="$(grep -F "\"$1\":" "$_lf" 2>/dev/null | head -n1 || true)"
  [ -n "$_line" ] || { echo ""; return 0; }
  printf '%s' "$_line" | sed -n \
    's/^[[:space:]]*"'"$1"'"[[:space:]]*:[[:space:]]*"\(.*\)"[[:space:]]*,\{0,1\}[[:space:]]*$/\1/p' \
    2>/dev/null || true
}

# t KEY [arg ...] -> uebersetzter, mit %s befuellter String auf stdout.
# Crasht NIE (set -eu-sicher). Fallback-Kette: gewaehlte Sprache -> en -> Key-Name.
t() {
  _key="$1"
  shift 2>/dev/null || true           # Rest ($@) sind die %s-Werte (0..n)
  _tpl="$(_lookup "$_key" "$CHARTS_LANG")"   # gewaehlte Sprache
  [ -n "$_tpl" ] || _tpl="$(_lookup "$_key" en)"   # Fallback en
  [ -n "$_tpl" ] || _tpl="$_key"      # Fallback: Key-Name selbst
  # Substitution. Schlaegt printf fehl (kaputtes Template), explizit das ROHE
  # Template ausgeben (printf '%s' = literal, NICHT echo wg. fuehrendem -n/-e).
  printf "$_tpl" "$@" 2>/dev/null || printf '%s' "$_tpl"
}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [charts-watchdog] $*"; }

# URL-Encoding fuer die Pushover-POST-Felder (title/message). BYTE-weise
# nach RFC 3986: unreservierte Zeichen (A-Z a-z 0-9 - . _ ~) bleiben, alles
# andere wird %XX. Byte-weise (ueber od -tx1), NICHT zeichenweise - sonst
# landen deutsche Umlaute (ae/oe/ue/ss, in UTF-8 je zwei Bytes) roh/kaputt im
# POST-Body. So kommen "ueber"->"über", "gueltig"->"gültig" etc. korrekt auf
# dem Handy an. (Ersetzt das fruehere reine Leerzeichen->%20; der alte
# Container-Meldungstext war ASCII und wird davon identisch kodiert.)
enc() {
  printf '%s' "$1" | od -An -tx1 | tr ' ' '\n' | sed '/^$/d' | while IFS= read -r _b; do
    case "$_b" in
      3[0-9]|4[1-9a-f]|5[0-9a]|6[1-9a-f]|7[0-9a]) printf '%b' "\\x$_b" ;;  # 0-9 A-Z a-z
      2d|2e|5f|7e) printf '%b' "\\x$_b" ;;                                 # - . _ ~
      *) printf '%%%s' "$_b" ;;
    esac
  done
}

# Sendet genau EINEN Pushover-Request mit dem fertigen POST-Body ($1) und
# loggt mit Titel ($2)/Text ($3) wie bisher inkl. Cert-Fallback.
_post() {
  _data="$1"
  _url="https://api.pushover.net/1/messages.json"
  # Erst normal (mit Zertifikatspruefung). Falls das Minimal-Image (Alpine/
  # BusyBox) kein CA-Bundle hat, scheitert HTTPS - dann als Fallback ohne
  # Zertifikatspruefung erneut versuchen (Pushover-Zertifikat ist gueltig; der
  # Fallback deckt nur das fehlende CA-Bundle ab, die Daten sind unkritisch).
  if wget -q -O /dev/null --post-data "$_data" "$_url" 2>/dev/null; then
    log "$(t watchdog.pushover.sent "$2" "$3")"
  elif wget -q -O /dev/null --no-check-certificate --post-data "$_data" "$_url" 2>/dev/null; then
    log "$(t watchdog.pushover.sent_nocert "$2" "$3")"
  else
    log "$(t watchdog.pushover.failed "$2")"
  fi
}

# Haelt die gueltigen Pushover-Gerätenamen des Accounts gecacht in
# /tmp/wd/.pushover-devices (eine pro Zeile) + Zeitstempel in
# /tmp/wd/.pushover-devices.ts. Grund: Pushover verwirft einen Request mit
# einem UNBEKANNTEN/deaktivierten device-Namen NICHT, sondern stellt ihn
# ersatzweise an ALLE Geräte zu -> ein toter Name in der Liste erzeugt bei
# jedem Empfänger einen Doppel-Push. Daher filtern wir vor dem Senden gegen
# diese Liste.
# Echo: "ok" wenn eine gueltige Liste bereitsteht (Cache frisch ODER frisch
# geholt + geparst), sonst "fail" (FAIL-OPEN: der Aufrufer filtert dann NICHT).
# set -eu-sicher: jeder Schritt mit `|| true`, kein Abbruch. Der Validate-Call
# verbraucht KEIN Nachrichten-Budget und stoert das uebrige Monitoring nicht
# (eigene Merker-Dateien, keine Beruehrung der .alarm/.bad/.good-Zustaende).
_valid_pushover_devices() {
  mkdir -p /tmp/wd 2>/dev/null || true
  _devf="/tmp/wd/.pushover-devices"
  _devts="/tmp/wd/.pushover-devices.ts"
  _now="$(date +%s 2>/dev/null || echo 0)"
  # Cache noch frisch? Dann die vorhandene Liste nutzen.
  _ts="$(cat "$_devts" 2>/dev/null || echo 0)"
  case "$_ts" in ''|*[!0-9]*) _ts=0 ;; esac
  if [ -s "$_devf" ] && [ "$((_now - _ts))" -lt "$PUSHOVER_DEVICES_TTL" ]; then
    echo ok
    return 0
  fi
  # Frisch holen (gleiche Aufrufform wie der Credential-Check am Schleifenende).
  _vf="/tmp/wd/.pov-dev.hdr.$$"
  _out="$(wget -q -S -O - --timeout=8 \
    --post-data "token=${PUSHOVER_TOKEN}&user=${PUSHOVER_USER}" \
    "https://api.pushover.net/1/users/validate.json" 2>"$_vf" || true)"
  _code="$(awk 'tolower($0) ~ /^ *http\// {c=$2} END {print c}' "$_vf" 2>/dev/null || true)"
  rm -f "$_vf" 2>/dev/null || true
  # Fehlendes CA-Bundle: einmal ohne Zertifikatspruefung erneut (wie in _post).
  if [ -z "$_code" ] || [ "$_code" != "200" ]; then
    _vf="/tmp/wd/.pov-dev.hdr.$$"
    _out="$(wget -q -S -O - --timeout=8 --no-check-certificate \
      --post-data "token=${PUSHOVER_TOKEN}&user=${PUSHOVER_USER}" \
      "https://api.pushover.net/1/users/validate.json" 2>"$_vf" || true)"
    _code="$(awk 'tolower($0) ~ /^ *http\// {c=$2} END {print c}' "$_vf" 2>/dev/null || true)"
    rm -f "$_vf" 2>/dev/null || true
  fi
  [ "$_code" = "200" ] || { echo fail; return 0; }
  # JSON-Feld "devices":[ "A","B",... ] ohne jq parsen: den Teil zwischen
  # "devices":[ und dem ersten ] ausschneiden, dann die gequoteten Namen
  # zeilenweise extrahieren.
  _arr="$(printf '%s' "$_out" | tr -d '\n' \
    | sed -n 's/.*"devices"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p' 2>/dev/null || true)"
  # Kein devices-Feld gefunden -> nicht sauber parsbar -> fail-open.
  case "$_out" in *'"devices"'*) : ;; *) echo fail; return 0 ;; esac
  _names="$(printf '%s' "$_arr" | grep -o '"[^"]*"' 2>/dev/null | tr -d '"' || true)"
  # Liste (auch leer) + Zeitstempel speichern. Eine leere, aber sauber
  # geparste Liste ist ein gueltiger Zustand (Account ohne Geräte).
  printf '%s\n' "$_names" > "$_devf" 2>/dev/null || true
  printf '%s\n' "$_now" > "$_devts" 2>/dev/null || true
  echo ok
  return 0
}

notify() {
  # $1 = Titel (Klartext), $2 = Text (Klartext)
  [ "$PUSHOVER_ENABLED" = "0" ] && return 0
  if [ -z "$PUSHOVER_TOKEN" ] || [ -z "$PUSHOVER_USER" ]; then
    log "$(t watchdog.pushover.not_configured "$1")"
    return 0
  fi
  _t="$(enc "$1")"
  _m="$(enc "$2")"
  _base="token=${PUSHOVER_TOKEN}&user=${PUSHOVER_USER}"
  _tail="&title=${_t}&message=${_m}"
  # Ton nur anhaengen, wenn gesetzt - sonst Account-Default (kein sound-Feld).
  # Wie title/message durch enc() URL-encoden (konsistent + robust gegen
  # ungewoehnliche Custom-Sound-Namen; bei den Standard-Toenen a-z unkritisch).
  [ -n "$PUSHOVER_SOUND" ] && _tail="${_tail}&sound=$(enc "$PUSHOVER_SOUND")"
  # PUSHOVER_DEVICE darf eine kommagetrennte Liste sein. Die Pushover-API
  # akzeptiert im device-Feld nur EINEN Gerätenamen pro Request, deshalb pro
  # Gerät ein eigener POST. Alle Geräte gehören zum selben Pushover-Account.
  # Leer => ein Request ohne device-Feld => alle Geräte des Accounts.
  if [ -z "$PUSHOVER_DEVICE" ]; then
    _post "${_base}${_tail}" "$1" "$2"
    return 0
  fi

  # Gueltige Geräteliste holen (gecacht). Nur filtern, wenn sie bereitsteht
  # (FAIL-OPEN: bei "fail" wie bisher an alle angefragten Geräte senden).
  _devok="$(_valid_pushover_devices || true)"
  _devf="/tmp/wd/.pushover-devices"
  _sent=0
  _dropped=""
  _OLDIFS="$IFS"
  IFS=,
  for _dev in $PUSHOVER_DEVICE; do
    IFS="$_OLDIFS"
    [ -n "$_dev" ] || { IFS=,; continue; }
    if [ "$_devok" = "ok" ]; then
      # Exakter Zeilen-Match gegen die gueltige Liste.
      if grep -qxF "$_dev" "$_devf" 2>/dev/null; then
        _post "${_base}&device=${_dev}${_tail}" "$1" "$2"
        _sent=$((_sent + 1))
      else
        _dropped="${_dropped:+$_dropped, }$_dev"
      fi
    else
      # Liste nicht abrufbar -> ungefiltert senden (fail-open).
      _post "${_base}&device=${_dev}${_tail}" "$1" "$2"
      _sent=$((_sent + 1))
    fi
    IFS=,
  done
  IFS="$_OLDIFS"
  [ -n "$_dropped" ] && log "$(t watchdog.pushover.devices_skipped "$_dropped")"
  # Es wurde gefiltert UND kein gueltiges Gerät blieb uebrig: NICHT device-los
  # senden (das ginge an alle) - lieber nichts senden.
  if [ "$_devok" = "ok" ] && [ "$_sent" -eq 0 ]; then
    log "$(t watchdog.pushover.devices_none_valid "$1")"
  fi
  return 0
}

# Fuehrt EINEN GET aus und befuellt HTTP_CODE (3-stellig; "000" = keine
# Antwort/Netz-/TLS-Fehler) und HTTP_BODY. FAIL-OPEN: scheitert NIE das Skript
# (wichtig, da `set -eu` aktiv ist - ein nicht-erreichbarer Dienst darf das
# Skript nicht beenden; daher ueberall `|| true`).
# BusyBox-wget (Alpine-Image docker:28-cli, kein curl) schreibt die Header
# inkl. Statuszeile mit -S auf stderr UND beendet sich bei HTTP 4xx/5xx mit
# Exit != 0 - darum lesen wir den Code aus der Statuszeile, NICHT aus dem
# Exit-Code (ListenBrainz 400 / iTunes 429 gelten als erreichbar). Timeout 8s.
# $1=URL, $2=optional ein Header ("Key: Wert").
http_probe() {
  _url="$1"; _hdr="${2:-}"
  _errf="/tmp/wd/.probe.hdr.$$"
  HTTP_CODE="000"; HTTP_BODY=""
  if [ -n "$_hdr" ]; then
    HTTP_BODY="$(wget -q -S -O - --timeout=8 --header "$_hdr" "$_url" 2>"$_errf" || true)"
  else
    HTTP_BODY="$(wget -q -S -O - --timeout=8 "$_url" 2>"$_errf" || true)"
  fi
  # Letzte HTTP/-Statuszeile aus den Headern (letzte wg. evtl. Redirects).
  _code="$(awk 'tolower($0) ~ /^ *http\// {c=$2} END {print c}' "$_errf" 2>/dev/null || true)"
  rm -f "$_errf" 2>/dev/null || true
  case "$_code" in
    [1-5][0-9][0-9]) HTTP_CODE="$_code" ;;
    *) HTTP_CODE="000" ;;
  esac
  # TLS-/CA-Fallback NUR, wenn gar keine Statuszeile kam (echter Netz-/TLS-
  # Fehler, kein 4xx). Deckt ein evtl. fehlendes CA-Bundle ab - genau wie der
  # Fallback in _post. Bei einem reinen 4xx wird NICHT erneut probiert.
  if [ "$HTTP_CODE" = "000" ]; then
    if [ -n "$_hdr" ]; then
      HTTP_BODY="$(wget -q -S -O - --timeout=8 --no-check-certificate --header "$_hdr" "$_url" 2>"$_errf" || true)"
    else
      HTTP_BODY="$(wget -q -S -O - --timeout=8 --no-check-certificate "$_url" 2>"$_errf" || true)"
    fi
    _code="$(awk 'tolower($0) ~ /^ *http\// {c=$2} END {print c}' "$_errf" 2>/dev/null || true)"
    rm -f "$_errf" 2>/dev/null || true
    case "$_code" in
      [1-5][0-9][0-9]) HTTP_CODE="$_code" ;;
      *) HTTP_CODE="000" ;;
    esac
  fi
}

# Erreichbarkeit EINES externen Dienstes. Echo: "ok", "problem" oder "skip"
# (= nicht konfiguriert -> nicht ueberwachen). Kriterien 1:1 aus collector.py.
# Fail-open: eine fehlende Antwort (HTTP_CODE=000) ist "problem" fuer DIESEN
# Poll (erst die Debounce-Maschine macht daraus ggf. eine Meldung).
ext_reachable() {
  case "$1" in
    slskd)
      # GET {SLSKD_HOST}/api/v0/server mit X-API-Key -> Body "isLoggedIn":true.
      [ -n "$SLSKD_API_KEY" ] || { echo skip; return; }
      http_probe "${SLSKD_HOST%/}/api/v0/server" "X-API-Key: $SLSKD_API_KEY"
      case "$HTTP_BODY" in
        *'"isLoggedIn":true'*) echo ok ;;
        *) echo problem ;;
      esac ;;
    lidarr)
      # Nur wenn URL+Key gesetzt. GET /api/v1/system/status -> HTTP 2xx.
      { [ -n "$LIDARR_URL" ] && [ -n "$LIDARR_API_KEY" ]; } || { echo skip; return; }
      http_probe "${LIDARR_URL%/}/api/v1/system/status" "X-Api-Key: $LIDARR_API_KEY"
      case "$HTTP_CODE" in 2[0-9][0-9]) echo ok ;; *) echo problem ;; esac ;;
    listenbrainz)
      # validate-token OHNE Token -> JEDE Antwort (auch 400) = erreichbar;
      # nur ein echter Netz-/TLS-Fehler (kein Code) ist "problem".
      http_probe "https://api.listenbrainz.org/1/validate-token"
      case "$HTTP_CODE" in 000) echo problem ;; *) echo ok ;; esac ;;
    deezer)
      # HTTP 200 UND Body ohne "error"-Feld (Deezer meldet Fehler als 200+error).
      http_probe "https://api.deezer.com/chart/0/tracks?limit=1"
      if [ "$HTTP_CODE" = "200" ]; then
        case "$HTTP_BODY" in *'"error"'*) echo problem ;; *) echo ok ;; esac
      else echo problem; fi ;;
    lastfm)
      # Nur mit API-Key. HTTP 200 UND Body ohne "error"-Feld.
      [ -n "$LASTFM_API_KEY" ] || { echo skip; return; }
      http_probe "https://ws.audioscrobbler.com/2.0/?method=chart.gettoptracks&limit=1&format=json&api_key=${LASTFM_API_KEY}"
      if [ "$HTTP_CODE" = "200" ]; then
        case "$HTTP_BODY" in *'"error"'*) echo problem ;; *) echo ok ;; esac
      else echo problem; fi ;;
    itunes)
      # 200 = ok; 429/403 = gedrosselt, TROTZDEM erreichbar (kein Alarm).
      http_probe "https://itunes.apple.com/search?term=test&limit=1"
      case "$HTTP_CODE" in 200|429|403) echo ok ;; *) echo problem ;; esac ;;
    *) echo skip ;;
  esac
}

# Menschlicher Name eines externen Dienstes fuer Log-/Meldungstexte.
ext_label() {
  case "$1" in
    slskd)        echo "slskd" ;;
    lidarr)       echo "Lidarr" ;;
    listenbrainz) echo "ListenBrainz" ;;
    deezer)       echo "Deezer" ;;
    lastfm)       echo "Last.fm" ;;
    itunes)       echo "iTunes" ;;
    *)            echo "$1" ;;
  esac
}

# Kombinierter Zustand eines Containers als ein Wort:
#   running-healthy | running-unhealthy | running-starting | running
#   restarting | exited | dead | paused | missing
state() {
  _c="$1"
  # .State.Status (running/exited/restarting/...) + ggf. .State.Health.Status.
  docker inspect --format \
    '{{.State.Status}}{{if .State.Health}}-{{.State.Health.Status}}{{end}}' \
    "$_c" 2>/dev/null || echo "missing"
}

# Klassifiziert einen Zustand:
#   ok          -> alles gut (kein Alarm)
#   transient   -> Uebergang (created/starting/restarting) - noch KEIN Alarm,
#                  erst wenn es anhaelt (siehe Entprellung unten)
#   problem     -> echtes Problem (unhealthy/exited/dead/paused/missing)
classify() {
  case "$1" in
    running-healthy|running) echo "ok" ;;        # running ohne Healthcheck = ok
    running-starting|starting|created|restarting) echo "transient" ;;
    *) echo "problem" ;;
  esac
}

# Menschliche Kurzbeschreibung fuer die Meldung.
describe() {
  case "$1" in
    running-unhealthy) t watchdog.state.unhealthy ;;
    restarting)        t watchdog.state.restarting ;;
    exited)            t watchdog.state.exited ;;
    dead)              t watchdog.state.dead ;;
    paused)            t watchdog.state.paused ;;
    missing)           t watchdog.state.missing ;;
    *)                 echo "$1" ;;
  esac
}

# --- Anti-Spam-/Anti-Flapping-Parameter -----------------------------------
# DEBOUNCE: Problem erst melden, wenn ein nicht-ok Zustand so viele Polls am
#   Stueck anhaelt (gegen kurze Start-/Recreate-Zucker 'created'->'running').
DEBOUNCE="${WATCH_DEBOUNCE_POLLS:-2}"
# RECOVER_POLLS: Entwarnung erst nach so vielen OK-Polls am Stueck. Flippert
#   eine App (ok/problem/ok/...), wird dieser Zaehler staendig zurueckgesetzt
#   -> es kommt KEINE Entwarnung und damit auch keine erneute Problem-Meldung.
RECOVER_POLLS="${WATCH_RECOVER_POLLS:-3}"
# COOLDOWN: Nach einer Problem-Meldung fuer diese App mindestens so viele
#   Sekunden KEINE weitere Problem-Meldung - egal wie oft sie in der Zeit
#   flippert. Default 1800 = 30 min.
COOLDOWN="${WATCH_COOLDOWN_SECONDS:-1800}"

# Externe Dienste: Debounce in SEKUNDEN (Default 600 = 10 min), in Polls
# umgerechnet (aufgerundet, mind. 1). Erst wenn ein externer Dienst so lange
# DURCHGEHEND nicht erreichbar ist, kommt EINE Meldung - kurze transiente
# Timeouts/einzelne Poll-Aussetzer melden so NIE sofort (kein Spam).
EXT_DEBOUNCE_SECONDS="${WATCH_EXTERNAL_DEBOUNCE_SECONDS:-600}"

read_num() { cat "$1" 2>/dev/null || echo 0; }

# Haupt-Ablauf: Start-Banner, Zustandsverzeichnis, Dauer-Schleife. Hinter dem
# Lib-Only-Schalter (siehe Dateiende), damit das Skript im Test gesourct werden
# kann, ohne die Schleife zu starten oder eine Streulog-Zeile / /tmp/wd zu
# erzeugen. Die EXT_DEBOUNCE_POLLS-Arithmetik haengt an INTERVAL und bleibt hier
# (laeuft erst beim echten Start, nicht beim Sourcen).
main_loop() {
  EXT_DEBOUNCE_POLLS=$(( (EXT_DEBOUNCE_SECONDS + INTERVAL - 1) / INTERVAL ))
  [ "$EXT_DEBOUNCE_POLLS" -lt 1 ] && EXT_DEBOUNCE_POLLS=1

  log "$(t watchdog.start.banner "$CONTAINERS" "$INTERVAL" "$DEBOUNCE" "$RECOVER_POLLS" "$COOLDOWN" "$EXT_DEBOUNCE_SECONDS" "$EXT_DEBOUNCE_POLLS")"

  # Zustandsmaschine je Container, Merker als Dateien unter /tmp/wd:
  #   <c>.alarm   : "1" solange ein Problem als GEMELDET gilt (bis Entwarnung)
  #   <c>.bad     : Zaehler aufeinanderfolgender nicht-ok Polls  (fuer DEBOUNCE)
  #   <c>.good    : Zaehler aufeinanderfolgender ok-Polls        (fuer RECOVER)
  #   <c>.last    : Unix-Zeit der letzten Problem-Meldung        (fuer COOLDOWN)
  mkdir -p /tmp/wd

  while true; do
    now="$(date +%s)"
    for c in $CONTAINERS; do
      cur="$(state "$c")"
      kind="$(classify "$cur")"
      alarmed="$(read_num "/tmp/wd/$c.alarm")"

      if [ "$kind" = "ok" ]; then
        echo 0 > "/tmp/wd/$c.bad"
        good="$(read_num "/tmp/wd/$c.good")"
        good=$((good + 1))
        echo "$good" > "/tmp/wd/$c.good"
        # Entwarnung NUR, wenn ein Alarm offen ist UND die App jetzt STABIL ok
        # ist (RECOVER_POLLS am Stueck). Flippern erreicht diese Schwelle nie.
        if [ "$alarmed" = "1" ] && [ "$good" -ge "$RECOVER_POLLS" ]; then
          log "$(t watchdog.log.recovered "$c" "$cur" "$good")"
          notify "$(t watchdog.notify.recovered_title "$c")" "$(t watchdog.notify.recovered_msg "$c" "$cur")"
          echo 0 > "/tmp/wd/$c.alarm"
        fi
      else
        echo 0 > "/tmp/wd/$c.good"
        bad="$(read_num "/tmp/wd/$c.bad")"
        bad=$((bad + 1))
        echo "$bad" > "/tmp/wd/$c.bad"
        # Alarm nur, wenn: lange genug schlecht (DEBOUNCE), noch kein offener
        # Alarm, UND der Cooldown seit der letzten Meldung abgelaufen ist.
        if [ "$bad" -ge "$DEBOUNCE" ] && [ "$alarmed" != "1" ]; then
          last="$(read_num "/tmp/wd/$c.last")"
          # Cooldown nur anwenden, wenn ueberhaupt schon mal gemeldet wurde
          # (last > 0). Der ERSTE Alarm darf nie unterdrueckt werden.
          if [ "$last" -eq 0 ] || [ "$((now - last))" -ge "$COOLDOWN" ]; then
            log "$(t watchdog.log.problem "$c" "$cur" "$bad")"
            notify "$(t watchdog.notify.problem_title "$c")" "$(t watchdog.notify.problem_msg "$c" "$(describe "$cur")")"
            echo 1 > "/tmp/wd/$c.alarm"
            echo "$now" > "/tmp/wd/$c.last"
          else
            # Im Cooldown: still vermerken, nicht melden.
            log "$(t watchdog.log.problem_cooldown "$c" "$cur")"
            echo 1 > "/tmp/wd/$c.alarm"
          fi
        fi
      fi
    done

    # --- Externe/abhaengige Dienste ----------------------------------------
    # Gleiche Anti-Spam-/Anti-Flapping-Maschine wie bei den Containern, aber mit
    # EIGENEM Namespace /tmp/wd/ext-* (sonst kollidiert z.B. der Container
    # "slskd" mit dem externen slskd-Check). Debounce = EXT_DEBOUNCE_POLLS
    # (= WATCH_EXTERNAL_DEBOUNCE_SECONDS, Default 10 min), Erholung RECOVER_POLLS,
    # Cooldown COOLDOWN - wiederverwendet aus dem Container-Teil.
    for e in slskd lidarr listenbrainz deezer lastfm itunes; do
      res="$(ext_reachable "$e")"
      [ "$res" = "skip" ] && continue   # nicht konfiguriert -> nicht ueberwachen
      label="$(ext_label "$e")"
      alarmed="$(read_num "/tmp/wd/ext-$e.alarm")"

      if [ "$res" = "ok" ]; then
        echo 0 > "/tmp/wd/ext-$e.bad"
        good="$(read_num "/tmp/wd/ext-$e.good")"
        good=$((good + 1))
        echo "$good" > "/tmp/wd/ext-$e.good"
        # Entwarnung nur bei offenem Alarm UND stabil wieder erreichbar.
        if [ "$alarmed" = "1" ] && [ "$good" -ge "$RECOVER_POLLS" ]; then
          log "$(t watchdog.log.ext_recovered "$label" "$good")"
          notify "$(t watchdog.notify.ext_recovered_title "$label")" "$(t watchdog.notify.ext_recovered_msg "$label")"
          echo 0 > "/tmp/wd/ext-$e.alarm"
        fi
      else
        echo 0 > "/tmp/wd/ext-$e.good"
        bad="$(read_num "/tmp/wd/ext-$e.bad")"
        bad=$((bad + 1))
        echo "$bad" > "/tmp/wd/ext-$e.bad"
        # Problem erst nach EXT_DEBOUNCE_POLLS durchgehend schlechten Polls
        # (= 10 min), kein offener Alarm, Cooldown seit letzter Meldung abgelaufen.
        if [ "$bad" -ge "$EXT_DEBOUNCE_POLLS" ] && [ "$alarmed" != "1" ]; then
          last="$(read_num "/tmp/wd/ext-$e.last")"
          if [ "$last" -eq 0 ] || [ "$((now - last))" -ge "$COOLDOWN" ]; then
            log "$(t watchdog.log.ext_problem "$label" "$bad")"
            notify "$(t watchdog.notify.ext_problem_title "$label")" "$(t watchdog.notify.ext_problem_msg "$label" "$EXT_DEBOUNCE_SECONDS")"
            echo 1 > "/tmp/wd/ext-$e.alarm"
            echo "$now" > "/tmp/wd/ext-$e.last"
          else
            log "$(t watchdog.log.ext_problem_cooldown "$label")"
            echo 1 > "/tmp/wd/ext-$e.alarm"
          fi
        fi
      fi
    done

    # Pushover: wird mit beruecksichtigt (geprueft + ins Container-Log), aber
    # BEWUSST NIEMALS per Pushover-Alarm gemeldet - eine Pushover-Stoerung per
    # Pushover melden zu wollen waere zirkulaer (vom Nutzer bestaetigte
    # Entscheidung). Daher KEINE .alarm/.bad/.good-Zustandsmaschine und KEIN
    # notify() fuer Pushover - nur ein log()-Eintrag pro Poll bei Problemen.
    # Der validate-Call (POST /1/users/validate.json) verbraucht KEIN Nachrichten-
    # Budget. Nur pruefen, wenn beide Credentials gesetzt sind. POST per
    # --post-data (NICHT als GET-Query), damit die Credentials nicht im
    # Request-Pfad/Log landen.
    if [ -n "$PUSHOVER_TOKEN" ] && [ -n "$PUSHOVER_USER" ]; then
      _povf="/tmp/wd/.pov.hdr.$$"
      _povout="$(wget -q -S -O - --timeout=8 \
        --post-data "token=${PUSHOVER_TOKEN}&user=${PUSHOVER_USER}" \
        "https://api.pushover.net/1/users/validate.json" 2>"$_povf" || true)"
      _povcode="$(awk 'tolower($0) ~ /^ *http\// {c=$2} END {print c}' "$_povf" 2>/dev/null || true)"
      rm -f "$_povf" 2>/dev/null || true
      case "$_povcode" in
        200)
          case "$_povout" in
            *'"status":1'*) : ;;  # Credentials gültig -> alles ok, nichts loggen
            *) log "$(t watchdog.pushover.cred_invalid)" ;;
          esac ;;
        "") log "$(t watchdog.pushover.unreachable)" ;;
        *)  log "$(t watchdog.pushover.unexpected "${_povcode}")" ;;
      esac
    fi

    sleep "$INTERVAL"
  done
}

# Lib-Only-Schalter: beim Sourcen fuer Tests (WATCHDOG_LIB_ONLY=1) wird die
# Hauptschleife NICHT gestartet - nur die Funktionen/Variablen stehen bereit
# (t(), _lookup(), LANGDIR, CHARTS_LANG). In Produktion ist der Schalter nie
# gesetzt -> main_loop laeuft wie bisher.
[ "${WATCHDOG_LIB_ONLY:-}" = 1 ] || main_loop
