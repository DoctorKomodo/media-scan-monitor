#!/bin/bash
#
# Plex partial-scan monitor.
#
# All configuration comes from environment variables (see docker-compose.yml).
# The only paths this script knows about are the *container-internal* media
# paths supplied via PLEX_LIBRARIES — the host-side source of each mount is a
# compose concern, independent of this script.
#
# Backward compatibility: if CONFIG_FILE points at an existing file it is
# sourced first, so the old plex_monitor.conf (PLEX_TOKEN=...) still works.

set -o pipefail

### Optional legacy config file (kept for bare-metal deployments)
CONFIG_FILE="${CONFIG_FILE:-}"
if [[ -n "$CONFIG_FILE" && -f "$CONFIG_FILE" ]]; then
    source "$CONFIG_FILE"
fi

### Configuration (env-driven, with defaults matching the original script)
PLEX_SERVER="${PLEX_SERVER:?PLEX_SERVER must be set (e.g. https://host:32400)}"
PLEX_LIBRARIES="${PLEX_LIBRARIES:?PLEX_LIBRARIES must be set (e.g. /data/media/movies:1,/data/media/tvseries:2)}"
WAIT_SEC="${WAIT_SEC:-30}"
MEDIA_EXTENSIONS="${MEDIA_EXTENSIONS:-mkv|mp4|avi|ts|m4v|mov|wmv|flv|webm|srt|smi|ssa|ass|sub|idx|sup|vtt}"
LOGFILE="${LOGFILE:-/dev/stdout}"
REQUIRED_INOTIFY_WATCHES="${REQUIRED_INOTIFY_WATCHES:-131072}"
INOTIFY_WAIT_MAX="${INOTIFY_WAIT_MAX:-180}"

### Token: prefer a secret file (Docker secret), fall back to PLEX_TOKEN env
if [[ -n "${PLEX_TOKEN_FILE:-}" && -f "$PLEX_TOKEN_FILE" ]]; then
    PLEX_TOKEN="$(< "$PLEX_TOKEN_FILE")"
    PLEX_TOKEN="${PLEX_TOKEN//$'\n'/}"   # strip trailing newline
fi
: "${PLEX_TOKEN:?PLEX_TOKEN not provided (set PLEX_TOKEN_FILE, PLEX_TOKEN, or CONFIG_FILE)}"

### Parse PLEX_LIBRARIES ("path:id,path:id") into the library map + watch list
declare -A LIBRARY_MAP
MONITOR_DIRS=()
IFS=',' read -ra _pairs <<< "$PLEX_LIBRARIES"
for _pair in "${_pairs[@]}"; do
    _pair="${_pair// /}"                 # tolerate spaces around commas
    [[ -z "$_pair" ]] && continue
    _path="${_pair%:*}"                  # everything before the last colon
    _id="${_pair##*:}"                   # the Plex section id
    if [[ -z "$_path" || -z "$_id" || "$_path" == "$_pair" ]]; then
        echo "[$(date)] ERROR: invalid PLEX_LIBRARIES entry: '$_pair'" >&2
        exit 1
    fi
    [[ "$_path" != */ ]] && _path="${_path}/"   # normalize trailing slash
    LIBRARY_MAP["$_path"]="$_id"
    MONITOR_DIRS+=("$_path")
done

### Parse IGNORE_DIRS (comma-separated)
IFS=',' read -ra IGNORE_DIRS <<< "${IGNORE_DIRS:-@eaDir,#snapshot}"

### Wait for inotify watch limit to be configured (host-level kernel setting).
### Set REQUIRED_INOTIFY_WATCHES=0 to skip this gate entirely.
if (( REQUIRED_INOTIFY_WATCHES > 0 )) && [[ -r /proc/sys/fs/inotify/max_user_watches ]]; then
    WAITED=0
    while (( $(cat /proc/sys/fs/inotify/max_user_watches) < REQUIRED_INOTIFY_WATCHES )); do
        if (( WAITED >= INOTIFY_WAIT_MAX )); then
            echo "[$(date)] ERROR: inotify watch limit below ${REQUIRED_INOTIFY_WATCHES} after ${INOTIFY_WAIT_MAX}s, exiting" >> "$LOGFILE"
            exit 1
        fi
        sleep 10
        ((WAITED += 10))
    done
fi

### Functions
urlencode() {
    local string="$1"
    python3 -c "import urllib.parse; print(urllib.parse.quote('''$string''', safe='/'))"
}

is_ignored_path() {
    local file_path="$1"

    for ignore_dir in "${IGNORE_DIRS[@]}"; do
        if [[ "$file_path" == *"/${ignore_dir}/"* ]]; then
            return 0  # True - should be ignored
        fi
    done

    return 1  # False - should not be ignored
}

send_plex_refresh() {
    local library_id="$1"
    local scan_path="$2"

    echo "[$(date)] === SENDING PLEX REFRESH ===" >> "$LOGFILE"
    echo "[$(date)] Library ID: $library_id" >> "$LOGFILE"
    echo "[$(date)] Scan path: $scan_path" >> "$LOGFILE"

    local encoded_path
    encoded_path=$(urlencode "$scan_path")

    local url="${PLEX_SERVER}/library/sections/${library_id}/refresh?path=${encoded_path}&X-Plex-Token=${PLEX_TOKEN}"

    echo "[$(date)] Request URL: ${PLEX_SERVER}/library/sections/${library_id}/refresh?path=${encoded_path}" >> "$LOGFILE"

    local curl_response
    curl_response=$(curl -s -w "HTTP_STATUS:%{http_code}" -X GET "$url" 2>&1)

    local http_status=$(echo "$curl_response" | grep -o "HTTP_STATUS:[0-9]*" | cut -d: -f2)

    echo "[$(date)] HTTP Status: $http_status" >> "$LOGFILE"

    if [[ "$http_status" == "200" ]]; then
        echo "[$(date)] ✅ Plex partial refresh successful!" >> "$LOGFILE"
    else
        echo "[$(date)] ❌ Plex refresh failed!" >> "$LOGFILE"
    fi
    echo "[$(date)] === END PLEX REFRESH ===" >> "$LOGFILE"
}

get_library_id() {
    local file_path="$1"

    for dir in "${!LIBRARY_MAP[@]}"; do
        if [[ "$file_path" == "$dir"* ]]; then
            echo "${LIBRARY_MAP[$dir]}"
            return
        fi
    done

    echo ""
}

get_library_root() {
    local file_path="$1"

    for dir in "${!LIBRARY_MAP[@]}"; do
        if [[ "$file_path" == "$dir"* ]]; then
            echo "$dir"
            return
        fi
    done

    echo ""
}

get_scan_path() {
    local file_path="$1"
    local library_root="$2"

    # Get path relative to library root
    local relative_path="${file_path#$library_root}"

    # Extract the top-level folder (show name or movie folder)
    local top_folder=$(echo "$relative_path" | cut -d'/' -f1)

    # Return the full path to that folder
    echo "${library_root}${top_folder}"
}

### Main Monitoring Logic
INCLUDE_PATTERN="\.(${MEDIA_EXTENSIONS})$"

echo "[$(date)] ========================================" >> "$LOGFILE"
echo "[$(date)] Starting Plex monitoring (targeted refresh)" >> "$LOGFILE"
echo "[$(date)] Plex server: ${PLEX_SERVER}" >> "$LOGFILE"
echo "[$(date)] Monitoring: ${MONITOR_DIRS[*]}" >> "$LOGFILE"
echo "[$(date)] Ignoring: ${IGNORE_DIRS[*]}" >> "$LOGFILE"
echo "[$(date)] Debounce interval: ${WAIT_SEC}s" >> "$LOGFILE"
echo "[$(date)] ========================================" >> "$LOGFILE"

# Track last refresh per scan path for debouncing
declare -A LAST_REFRESH_TIME

inotifywait -m -r -e create -e moved_to -e delete -e move \
    --format $'%e\t%w%f' "${MONITOR_DIRS[@]}" | \
while IFS=$'\t' read -r EVENT FULLPATH; do

    # Check if file matches our media extensions
    if [[ ! "$FULLPATH" =~ $INCLUDE_PATTERN ]]; then
        continue
    fi

    # Check if path is in an ignored directory
    if is_ignored_path "$FULLPATH"; then
        continue
    fi

    echo "[$(date)] Detected: $FULLPATH [$EVENT]" >> "$LOGFILE"

    # Get library info
    LIBRARY_ID=$(get_library_id "$FULLPATH")
    LIBRARY_ROOT=$(get_library_root "$FULLPATH")

    if [[ -z "$LIBRARY_ID" ]]; then
        echo "[$(date)] ❌ No library mapping found for: $FULLPATH" >> "$LOGFILE"
        continue
    fi

    # Determine the folder to scan (show folder or movie folder)
    SCAN_PATH=$(get_scan_path "$FULLPATH" "$LIBRARY_ROOT")

    echo "[$(date)] Library ID: $LIBRARY_ID" >> "$LOGFILE"
    echo "[$(date)] Scan path: $SCAN_PATH" >> "$LOGFILE"

    NOW=$(date +%s)
    LAST_TIME=${LAST_REFRESH_TIME[$SCAN_PATH]:-0}
    TIME_SINCE=$((NOW - LAST_TIME))

    echo "[$(date)] ${TIME_SINCE}s since last refresh of this path" >> "$LOGFILE"

    # Check if enough time has passed since last refresh for this specific path
    if (( TIME_SINCE >= WAIT_SEC )); then
        echo "[$(date)] ⏳ Waiting ${WAIT_SEC}s for additional changes..." >> "$LOGFILE"
        sleep $WAIT_SEC

        send_plex_refresh "$LIBRARY_ID" "$SCAN_PATH"
        LAST_REFRESH_TIME[$SCAN_PATH]=$(date +%s)
    else
        REMAINING=$((WAIT_SEC - TIME_SINCE))
        echo "[$(date)] ⏭️  Skipping (debounced, ${REMAINING}s remaining)" >> "$LOGFILE"
    fi

done
