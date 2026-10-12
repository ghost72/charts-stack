#!/bin/sh
# charts-watchdog - monitors ALL containers of the charts-stack and reports via
# Pushover when one switches into a bad state (and when it recovers again).
# Pure shell script, no build.
#
# What is reported (per container):
#   - unhealthy      : healthcheck fires (currently only slskd has one; future
#                      healthchecks are monitored automatically).
#   - restarting     : container is stuck in a restart loop (a real persistent
#                      problem - restart:unless-stopped does not come up).
#   - exited/dead    : container is (unexpectedly) stopped and stays that way.
#   - recovery       : back to running+healthy -> all-clear.
#
# Anti-spam / anti-flapping (important!): per problem episode there is EXACTLY
# ONE problem message and EXACTLY ONE all-clear. Concretely:
#   - A problem is only reported after DEBOUNCE consecutive bad polls.
#   - After a problem message a COOLDOWN applies: in that window no second
#     problem message for the same app - no matter how often it flaps.
#   - An all-clear only comes once the app is RECOVER_POLLS consecutive polls
#     ok. A flapping app (ok/problem/ok/...) never reaches that -> no all-clear
#     and therefore no repeated problem message. So you do NOT get constant
#     messages when an app is unstable.
#
# What CANNOT be reported: a container that is RUNNING but whose internal logic
# is stuck. That would need a dedicated healthcheck per service. For the
# scheduler services (run in a loop) the run/health state is the meaningful
# signal.
#
# Runs in a docker:cli container with a read-only Docker socket and polls the
# state via the local Docker engine.

set -eu

# Containers to monitor (space-separated). Default: the whole stack.
# Adjustable via WATCH_CONTAINERS in the .env/compose.
CONTAINERS="${WATCH_CONTAINERS:-slskd charts-sync charts-hitparade charts-flashback charts-download charts-import charts-recommend charts-upgrade charts-songrequest charts-digest}"
INTERVAL="${WATCH_INTERVAL_SECONDS:-30}"

PUSHOVER_TOKEN="${PUSHOVER_TOKEN:-}"
PUSHOVER_USER="${PUSHOVER_USER:-}"
PUSHOVER_DEVICE="${PUSHOVER_DEVICE:-}"
# Pushover notification sound. Empty = account default (no sound field in the
# request). Default for charts-watchdog: "siren".
PUSHOVER_SOUND="${PUSHOVER_SOUND:-siren}"
# Cache duration (seconds) for the device list fetched from the Pushover
# account (see _valid_pushover_devices). PUSHOVER_DEVICE is filtered against it
# before sending so an invalid/disabled device name does not trigger Pushover's
# "to everyone" fallback (double push). Default 3600s = 1h.
PUSHOVER_DEVICES_TTL="${PUSHOVER_DEVICES_TTL:-3600}"
# Mute Pushover for this service globally (0 = off). Default: on.
PUSHOVER_ENABLED="${PUSHOVER_ENABLED:-1}"

# --- External/dependent services -------------------------------------------
# Reachability checks for the services the charts-stack depends on
# (slskd, Lidarr, ListenBrainz, Deezer, Last.fm, iTunes, Pushover). Criteria
# 1:1 as in charts-dashboard (collector.py _slskd_status/_lidarr_status/
# _external_status). Lidarr + Last.fm are only monitored when configured.
SLSKD_HOST="${SLSKD_HOST:-http://slskd:5030}"
SLSKD_API_KEY="${SLSKD_API_KEY:-}"
LIDARR_URL="${LIDARR_URL:-}"
LIDARR_API_KEY="${LIDARR_API_KEY:-}"
LASTFM_API_KEY="${LASTFM_API_KEY:-}"

# --- i18n (translatable output texts, shared lang/ set) ---------------------
# charts-watchdog is the only non-Python service; t() mirrors the semantics of
# the Python loader (path search, chosen language -> en -> key name, never
# crash) but stays BusyBox-capable (only grep/sed/printf). The watchdog.* keys
# use positional %s + printf (NOT named {name}).
#
# ONE-TIME normalization at script start (set -u-safe): CHARTS_LANG and LANGDIR
# are set here top-level in variables and after that only read per t() call -
# no bare $CHARTS_LANG, no tr per call. MUST run before the first t() user (the
# start banner is in main_loop(), so safely after this line).
CHARTS_LANG="$(printf '%s' "${CHARTS_LANG:-}" | tr '[:upper:]' '[:lower:]' 2>/dev/null || true)"
# Language directory in Python priority: $CHARTS_LANG_DIR -> /lang -> $0-relative
# (../lang, ./lang). The first existing hit wins; default /lang (t() then
# degrades cleanly to the key name). The $0 candidates are only relevant for the
# dev/standalone case (in the container $0=/watch.sh -> ../lang=./lang=/lang).
LANGDIR="/lang"
_wd_scriptdir="$(dirname "$0" 2>/dev/null || echo .)"
for _wd_cand in "${CHARTS_LANG_DIR:-}" "/lang" "${_wd_scriptdir}/../lang" "${_wd_scriptdir}/lang"; do
  [ -n "$_wd_cand" ] || continue
  if [ -d "$_wd_cand" ]; then
    LANGDIR="$_wd_cand"
    break
  fi
done

# _lookup KEY LANG -> template for KEY from LANG.json, otherwise empty. Every
# step with `|| true` (set -eu-safe). Two-step grep->sed:
#   Step 1: isolate the ONE key line via grep -F "<key>": (delimiter with a
#     closing quote AND colon against prefix collision, e.g.
#     watchdog.notify.problem vs. ...problem_title) + head -n1 (one-line contract).
#   Step 2: greedily cut out the value between the value quotes (anchored on the
#     last " of the line; an optional trailing comma is allowed).
_lookup() {
  _lf="$LANGDIR/$2.json"
  [ -f "$_lf" ] || { echo ""; return 0; }
  _line="$(grep -F "\"$1\":" "$_lf" 2>/dev/null | head -n1 || true)"
  [ -n "$_line" ] || { echo ""; return 0; }
  printf '%s' "$_line" | sed -n \
    's/^[[:space:]]*"'"$1"'"[[:space:]]*:[[:space:]]*"\(.*\)"[[:space:]]*,\{0,1\}[[:space:]]*$/\1/p' \
    2>/dev/null || true
}

# t KEY [arg ...] -> translated string filled with %s, on stdout.
# NEVER crashes (set -eu-safe). Fallback chain: chosen language -> en -> key name.
t() {
  _key="$1"
  shift 2>/dev/null || true           # the rest ($@) are the %s values (0..n)
  _tpl="$(_lookup "$_key" "$CHARTS_LANG")"   # chosen language
  [ -n "$_tpl" ] || _tpl="$(_lookup "$_key" en)"   # fallback en
  [ -n "$_tpl" ] || _tpl="$_key"      # fallback: the key name itself
  # Substitution. If printf fails (broken template), explicitly output the RAW
  # template (printf '%s' = literal, NOT echo because of a leading -n/-e).
  printf "$_tpl" "$@" 2>/dev/null || printf '%s' "$_tpl"
}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [charts-watchdog] $*"; }

# URL encoding for the Pushover POST fields (title/message). BYTE-wise per
# RFC 3986: unreserved characters (A-Z a-z 0-9 - . _ ~) stay, everything else
# becomes %XX. Byte-wise (via od -tx1), NOT character-wise - otherwise German
# umlauts (ae/oe/ue/ss, two bytes each in UTF-8) end up raw/broken in the POST
# body. This way "ueber"->"über", "gueltig"->"gültig" etc. arrive correctly on
# the phone. (Replaces the former plain space->%20; the old container message
# text was ASCII and is encoded identically by this.)
enc() {
  printf '%s' "$1" | od -An -tx1 | tr ' ' '\n' | sed '/^$/d' | while IFS= read -r _b; do
    case "$_b" in
      3[0-9]|4[1-9a-f]|5[0-9a]|6[1-9a-f]|7[0-9a]) printf '%b' "\\x$_b" ;;  # 0-9 A-Z a-z
      2d|2e|5f|7e) printf '%b' "\\x$_b" ;;                                 # - . _ ~
      *) printf '%%%s' "$_b" ;;
    esac
  done
}

# Sends exactly ONE Pushover request with the finished POST body ($1) and logs
# with title ($2)/text ($3) as before, incl. cert fallback.
_post() {
  _data="$1"
  _url="https://api.pushover.net/1/messages.json"
  # First normally (with certificate verification). If the minimal image
  # (Alpine/BusyBox) has no CA bundle, HTTPS fails - then retry as a fallback
  # without certificate verification (the Pushover certificate is valid; the
  # fallback only covers the missing CA bundle, the data is uncritical).
  if wget -q -O /dev/null --post-data "$_data" "$_url" 2>/dev/null; then
    log "$(t watchdog.pushover.sent "$2" "$3")"
  elif wget -q -O /dev/null --no-check-certificate --post-data "$_data" "$_url" 2>/dev/null; then
    log "$(t watchdog.pushover.sent_nocert "$2" "$3")"
  else
    log "$(t watchdog.pushover.failed "$2")"
  fi
}

# Keeps the valid Pushover device names of the account cached in
# /tmp/wd/.pushover-devices (one per line) + timestamp in
# /tmp/wd/.pushover-devices.ts. Reason: Pushover does NOT discard a request
# with an UNKNOWN/disabled device name but delivers it to ALL devices instead
# -> a dead name in the list produces a double push for every recipient. So we
# filter against this list before sending.
# Echo: "ok" if a valid list is available (cache fresh OR freshly fetched +
# parsed), otherwise "fail" (FAIL-OPEN: the caller then does NOT filter).
# set -eu-safe: every step with `|| true`, no abort. The validate call consumes
# NO message budget and does not disturb the rest of the monitoring (own marker
# files, no touching of the .alarm/.bad/.good states).
_valid_pushover_devices() {
  mkdir -p /tmp/wd 2>/dev/null || true
  _devf="/tmp/wd/.pushover-devices"
  _devts="/tmp/wd/.pushover-devices.ts"
  _now="$(date +%s 2>/dev/null || echo 0)"
  # Cache still fresh? Then use the existing list.
  _ts="$(cat "$_devts" 2>/dev/null || echo 0)"
  case "$_ts" in ''|*[!0-9]*) _ts=0 ;; esac
  if [ -s "$_devf" ] && [ "$((_now - _ts))" -lt "$PUSHOVER_DEVICES_TTL" ]; then
    echo ok
    return 0
  fi
  # Fetch fresh (same call form as the credential check at the loop end).
  _vf="/tmp/wd/.pov-dev.hdr.$$"
  _out="$(wget -q -S -O - --timeout=8 \
    --post-data "token=${PUSHOVER_TOKEN}&user=${PUSHOVER_USER}" \
    "https://api.pushover.net/1/users/validate.json" 2>"$_vf" || true)"
  _code="$(awk 'tolower($0) ~ /^ *http\// {c=$2} END {print c}' "$_vf" 2>/dev/null || true)"
  rm -f "$_vf" 2>/dev/null || true
  # Missing CA bundle: retry once without certificate verification (as in _post).
  if [ -z "$_code" ] || [ "$_code" != "200" ]; then
    _vf="/tmp/wd/.pov-dev.hdr.$$"
    _out="$(wget -q -S -O - --timeout=8 --no-check-certificate \
      --post-data "token=${PUSHOVER_TOKEN}&user=${PUSHOVER_USER}" \
      "https://api.pushover.net/1/users/validate.json" 2>"$_vf" || true)"
    _code="$(awk 'tolower($0) ~ /^ *http\// {c=$2} END {print c}' "$_vf" 2>/dev/null || true)"
    rm -f "$_vf" 2>/dev/null || true
  fi
  [ "$_code" = "200" ] || { echo fail; return 0; }
  # Parse the JSON field "devices":[ "A","B",... ] without jq: cut out the part
  # between "devices":[ and the first ], then extract the quoted names line by
  # line.
  _arr="$(printf '%s' "$_out" | tr -d '\n' \
    | sed -n 's/.*"devices"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p' 2>/dev/null || true)"
  # No devices field found -> not cleanly parsable -> fail-open.
  case "$_out" in *'"devices"'*) : ;; *) echo fail; return 0 ;; esac
  _names="$(printf '%s' "$_arr" | grep -o '"[^"]*"' 2>/dev/null | tr -d '"' || true)"
  # Store the list (even empty) + timestamp. An empty but cleanly parsed list
  # is a valid state (account without devices).
  printf '%s\n' "$_names" > "$_devf" 2>/dev/null || true
  printf '%s\n' "$_now" > "$_devts" 2>/dev/null || true
  echo ok
  return 0
}

notify() {
  # $1 = title (plain text), $2 = text (plain text)
  [ "$PUSHOVER_ENABLED" = "0" ] && return 0
  if [ -z "$PUSHOVER_TOKEN" ] || [ -z "$PUSHOVER_USER" ]; then
    log "$(t watchdog.pushover.not_configured "$1")"
    return 0
  fi
  _t="$(enc "$1")"
  _m="$(enc "$2")"
  _base="token=${PUSHOVER_TOKEN}&user=${PUSHOVER_USER}"
  _tail="&title=${_t}&message=${_m}"
  # Only append the sound if set - otherwise account default (no sound field).
  # URL-encode via enc() like title/message (consistent + robust against
  # unusual custom sound names; for the standard a-z tones uncritical).
  [ -n "$PUSHOVER_SOUND" ] && _tail="${_tail}&sound=$(enc "$PUSHOVER_SOUND")"
  # PUSHOVER_DEVICE may be a comma-separated list. The Pushover API accepts only
  # ONE device name per request in the device field, so one POST per device.
  # All devices belong to the same Pushover account.
  # Empty => one request without a device field => all devices of the account.
  if [ -z "$PUSHOVER_DEVICE" ]; then
    _post "${_base}${_tail}" "$1" "$2"
    return 0
  fi

  # Fetch the valid device list (cached). Only filter if it is available
  # (FAIL-OPEN: on "fail" send to all requested devices as before).
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
      # Exact line match against the valid list.
      if grep -qxF "$_dev" "$_devf" 2>/dev/null; then
        _post "${_base}&device=${_dev}${_tail}" "$1" "$2"
        _sent=$((_sent + 1))
      else
        _dropped="${_dropped:+$_dropped, }$_dev"
      fi
    else
      # List not retrievable -> send unfiltered (fail-open).
      _post "${_base}&device=${_dev}${_tail}" "$1" "$2"
      _sent=$((_sent + 1))
    fi
    IFS=,
  done
  IFS="$_OLDIFS"
  [ -n "$_dropped" ] && log "$(t watchdog.pushover.devices_skipped "$_dropped")"
  # Filtering happened AND no valid device remained: do NOT send device-less
  # (that would go to everyone) - rather send nothing.
  if [ "$_devok" = "ok" ] && [ "$_sent" -eq 0 ]; then
    log "$(t watchdog.pushover.devices_none_valid "$1")"
  fi
  return 0
}

# Runs ONE GET and fills HTTP_CODE (3-digit; "000" = no response/network/TLS
# error) and HTTP_BODY. FAIL-OPEN: NEVER fails the script (important, since
# `set -eu` is active - an unreachable service must not terminate the script;
# hence `|| true` everywhere).
# BusyBox wget (Alpine image docker:28-cli, no curl) writes the headers incl.
# status line with -S to stderr AND exits with a non-zero code on HTTP 4xx/5xx
# - so we read the code from the status line, NOT from the exit code
# (ListenBrainz 400 / iTunes 429 count as reachable). Timeout 8s.
# $1=URL, $2=optional one header ("Key: value").
http_probe() {
  _url="$1"; _hdr="${2:-}"
  _errf="/tmp/wd/.probe.hdr.$$"
  HTTP_CODE="000"; HTTP_BODY=""
  if [ -n "$_hdr" ]; then
    HTTP_BODY="$(wget -q -S -O - --timeout=8 --header "$_hdr" "$_url" 2>"$_errf" || true)"
  else
    HTTP_BODY="$(wget -q -S -O - --timeout=8 "$_url" 2>"$_errf" || true)"
  fi
  # Last HTTP/ status line from the headers (last one because of possible redirects).
  _code="$(awk 'tolower($0) ~ /^ *http\// {c=$2} END {print c}' "$_errf" 2>/dev/null || true)"
  rm -f "$_errf" 2>/dev/null || true
  case "$_code" in
    [1-5][0-9][0-9]) HTTP_CODE="$_code" ;;
    *) HTTP_CODE="000" ;;
  esac
  # TLS/CA fallback ONLY if no status line came at all (a real network/TLS
  # error, not a 4xx). Covers a possibly missing CA bundle - just like the
  # fallback in _post. On a plain 4xx it is NOT retried.
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

# Reachability of ONE external service. Echo: "ok", "problem" or "skip"
# (= not configured -> do not monitor). Criteria 1:1 from collector.py.
# Fail-open: a missing response (HTTP_CODE=000) is "problem" for THIS poll
# (only the debounce machine may turn it into a message).
ext_reachable() {
  case "$1" in
    slskd)
      # GET {SLSKD_HOST}/api/v0/server with X-API-Key -> body "isLoggedIn":true.
      [ -n "$SLSKD_API_KEY" ] || { echo skip; return; }
      http_probe "${SLSKD_HOST%/}/api/v0/server" "X-API-Key: $SLSKD_API_KEY"
      case "$HTTP_BODY" in
        *'"isLoggedIn":true'*) echo ok ;;
        *) echo problem ;;
      esac ;;
    lidarr)
      # Only if URL+key set. GET /api/v1/system/status -> HTTP 2xx.
      { [ -n "$LIDARR_URL" ] && [ -n "$LIDARR_API_KEY" ]; } || { echo skip; return; }
      http_probe "${LIDARR_URL%/}/api/v1/system/status" "X-Api-Key: $LIDARR_API_KEY"
      case "$HTTP_CODE" in 2[0-9][0-9]) echo ok ;; *) echo problem ;; esac ;;
    listenbrainz)
      # validate-token WITHOUT a token -> ANY response (even 400) = reachable;
      # only a real network/TLS error (no code) is "problem".
      http_probe "https://api.listenbrainz.org/1/validate-token"
      case "$HTTP_CODE" in 000) echo problem ;; *) echo ok ;; esac ;;
    deezer)
      # HTTP 200 AND body without "error" field (Deezer reports errors as 200+error).
      http_probe "https://api.deezer.com/chart/0/tracks?limit=1"
      if [ "$HTTP_CODE" = "200" ]; then
        case "$HTTP_BODY" in *'"error"'*) echo problem ;; *) echo ok ;; esac
      else echo problem; fi ;;
    lastfm)
      # Only with an API key. HTTP 200 AND body without "error" field.
      [ -n "$LASTFM_API_KEY" ] || { echo skip; return; }
      http_probe "https://ws.audioscrobbler.com/2.0/?method=chart.gettoptracks&limit=1&format=json&api_key=${LASTFM_API_KEY}"
      if [ "$HTTP_CODE" = "200" ]; then
        case "$HTTP_BODY" in *'"error"'*) echo problem ;; *) echo ok ;; esac
      else echo problem; fi ;;
    itunes)
      # 200 = ok; 429/403 = throttled, STILL reachable (no alarm).
      http_probe "https://itunes.apple.com/search?term=test&limit=1"
      case "$HTTP_CODE" in 200|429|403) echo ok ;; *) echo problem ;; esac ;;
    *) echo skip ;;
  esac
}

# Human name of an external service for log/message texts.
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

# Combined state of a container as one word:
#   running-healthy | running-unhealthy | running-starting | running
#   restarting | exited | dead | paused | missing
state() {
  _c="$1"
  # .State.Status (running/exited/restarting/...) + optionally .State.Health.Status.
  docker inspect --format \
    '{{.State.Status}}{{if .State.Health}}-{{.State.Health.Status}}{{end}}' \
    "$_c" 2>/dev/null || echo "missing"
}

# Classifies a state:
#   ok          -> all good (no alarm)
#   transient   -> transition (created/starting/restarting) - NO alarm yet,
#                  only once it persists (see debounce below)
#   problem     -> real problem (unhealthy/exited/dead/paused/missing)
classify() {
  case "$1" in
    running-healthy|running) echo "ok" ;;        # running without a healthcheck = ok
    running-starting|starting|created|restarting) echo "transient" ;;
    *) echo "problem" ;;
  esac
}

# Human short description for the message.
describe() {
  case "$1" in
    running-unhealthy) t watchdog.state.unhealthy ;;
    restarting)        t watchdog.state.restarting ;;
    created|starting|running-starting) t watchdog.state.starting ;;
    exited)            t watchdog.state.exited ;;
    dead)              t watchdog.state.dead ;;
    paused)            t watchdog.state.paused ;;
    missing)           t watchdog.state.missing ;;
    *)                 echo "$1" ;;
  esac
}

# --- Anti-spam / anti-flapping parameters ----------------------------------
# DEBOUNCE: only report a problem once a non-ok state persists for this many
#   polls in a row (against short start/recreate hiccups 'created'->'running').
DEBOUNCE="${WATCH_DEBOUNCE_POLLS:-2}"
# TRANSIENT_DEBOUNCE: a transient state (created/starting/restarting) is a
#   normal part of a rebuild and must NOT alarm at the short DEBOUNCE - a
#   'created'->'running' transition during `docker compose up` would otherwise
#   trigger a false "Problem". So a transient state only alarms once it has
#   PERSISTED for this (much higher) number of consecutive polls, which still
#   catches a container genuinely STUCK transient (e.g. a real restart loop) so
#   it is never silently ignored. A real problem (unhealthy/exited/dead/...)
#   keeps the normal DEBOUNCE.
TRANSIENT_DEBOUNCE="${WATCH_TRANSIENT_DEBOUNCE_POLLS:-8}"
# RECOVER_POLLS: all-clear only after this many OK polls in a row. If an app
#   flaps (ok/problem/ok/...), this counter is reset constantly -> NO all-clear
#   and therefore no repeated problem message.
RECOVER_POLLS="${WATCH_RECOVER_POLLS:-3}"
# COOLDOWN: after a problem message for this app, NO further problem message for
#   at least this many seconds - no matter how often it flaps during that time.
#   Default 1800 = 30 min.
COOLDOWN="${WATCH_COOLDOWN_SECONDS:-1800}"

# External services: debounce in SECONDS (default 600 = 10 min), converted to
# polls (rounded up, min. 1). Only once an external service is UNREACHABLE
# continuously for that long does ONE message come - short transient
# timeouts/single poll dropouts thus NEVER report immediately (no spam).
EXT_DEBOUNCE_SECONDS="${WATCH_EXTERNAL_DEBOUNCE_SECONDS:-600}"

read_num() { cat "$1" 2>/dev/null || echo 0; }

# Main flow: start banner, state directory, continuous loop. Behind the
# lib-only switch (see end of file) so the script can be sourced in tests
# without starting the loop or producing a stray log line / /tmp/wd. The
# EXT_DEBOUNCE_POLLS arithmetic depends on INTERVAL and stays here (only runs on
# a real start, not when sourced).
main_loop() {
  EXT_DEBOUNCE_POLLS=$(( (EXT_DEBOUNCE_SECONDS + INTERVAL - 1) / INTERVAL ))
  [ "$EXT_DEBOUNCE_POLLS" -lt 1 ] && EXT_DEBOUNCE_POLLS=1

  log "$(t watchdog.start.banner "$CONTAINERS" "$INTERVAL" "$DEBOUNCE" "$RECOVER_POLLS" "$COOLDOWN" "$EXT_DEBOUNCE_SECONDS" "$EXT_DEBOUNCE_POLLS")"

  # State machine per container, markers as files under /tmp/wd:
  #   <c>.alarm   : "1" while a problem counts as REPORTED (until all-clear)
  #   <c>.bad     : counter of consecutive non-ok polls   (for DEBOUNCE)
  #   <c>.good    : counter of consecutive ok polls        (for RECOVER)
  #   <c>.last    : Unix time of the last problem message  (for COOLDOWN)
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
        # All-clear ONLY if an alarm is open AND the app is now STABLY ok
        # (RECOVER_POLLS in a row). Flapping never reaches this threshold.
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
        # Threshold depends on the kind of non-ok state: a transient state
        # (created/starting/restarting) is expected during a rebuild and only
        # alarms after the HIGHER TRANSIENT_DEBOUNCE (stuck-transient guard),
        # whereas a real problem keeps the normal, short DEBOUNCE.
        if [ "$kind" = "transient" ]; then
          _thr="$TRANSIENT_DEBOUNCE"
        else
          _thr="$DEBOUNCE"
        fi
        # Alarm only if: bad for long enough (threshold), no open alarm yet, AND
        # the cooldown since the last message has expired.
        if [ "$bad" -ge "$_thr" ] && [ "$alarmed" != "1" ]; then
          last="$(read_num "/tmp/wd/$c.last")"
          # Only apply the cooldown if something was ever reported at all
          # (last > 0). The FIRST alarm must never be suppressed.
          if [ "$last" -eq 0 ] || [ "$((now - last))" -ge "$COOLDOWN" ]; then
            log "$(t watchdog.log.problem "$c" "$cur" "$bad")"
            notify "$(t watchdog.notify.problem_title "$c")" "$(t watchdog.notify.problem_msg "$c" "$(describe "$cur")")"
            echo 1 > "/tmp/wd/$c.alarm"
            echo "$now" > "/tmp/wd/$c.last"
          else
            # In cooldown: record quietly, do not report.
            log "$(t watchdog.log.problem_cooldown "$c" "$cur")"
            echo 1 > "/tmp/wd/$c.alarm"
          fi
        fi
      fi
    done

    # --- External/dependent services ---------------------------------------
    # Same anti-spam / anti-flapping machine as for the containers, but with its
    # OWN namespace /tmp/wd/ext-* (otherwise e.g. the container "slskd" would
    # collide with the external slskd check). Debounce = EXT_DEBOUNCE_POLLS
    # (= WATCH_EXTERNAL_DEBOUNCE_SECONDS, default 10 min), recovery RECOVER_POLLS,
    # cooldown COOLDOWN - reused from the container part.
    for e in slskd lidarr listenbrainz deezer lastfm itunes; do
      res="$(ext_reachable "$e")"
      [ "$res" = "skip" ] && continue   # not configured -> do not monitor
      label="$(ext_label "$e")"
      alarmed="$(read_num "/tmp/wd/ext-$e.alarm")"

      if [ "$res" = "ok" ]; then
        echo 0 > "/tmp/wd/ext-$e.bad"
        good="$(read_num "/tmp/wd/ext-$e.good")"
        good=$((good + 1))
        echo "$good" > "/tmp/wd/ext-$e.good"
        # All-clear only on an open alarm AND stably reachable again.
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
        # Problem only after EXT_DEBOUNCE_POLLS continuous bad polls (= 10 min),
        # no open alarm, cooldown since the last message expired.
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

    # Pushover: is taken into account (checked + into the container log) but
    # DELIBERATELY NEVER reported via a Pushover alarm - wanting to report a
    # Pushover outage via Pushover would be circular (user-confirmed decision).
    # Hence NO .alarm/.bad/.good state machine and NO notify() for Pushover -
    # only one log() entry per poll on problems. The validate call
    # (POST /1/users/validate.json) consumes NO message budget. Only check when
    # both credentials are set. POST via --post-data (NOT as a GET query) so the
    # credentials do not end up in the request path/log.
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
            *'"status":1'*) : ;;  # credentials valid -> all ok, log nothing
            *) log "$(t watchdog.pushover.cred_invalid)" ;;
          esac ;;
        "") log "$(t watchdog.pushover.unreachable)" ;;
        *)  log "$(t watchdog.pushover.unexpected "${_povcode}")" ;;
      esac
    fi

    sleep "$INTERVAL"
  done
}

# Lib-only switch: when sourced for tests (WATCHDOG_LIB_ONLY=1) the main loop
# is NOT started - only the functions/variables are available (t(), _lookup(),
# LANGDIR, CHARTS_LANG). In production the switch is never set -> main_loop runs
# as before.
[ "${WATCHDOG_LIB_ONLY:-}" = 1 ] || main_loop
