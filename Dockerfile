FROM alpine:3.20

# Runtime dependencies:
#   bash          - script uses bash v4+ associative arrays
#   inotify-tools - inotifywait for filesystem monitoring
#   curl          - Plex API calls
#   python3       - URL-encoding of scan paths
#   tzdata        - correct timestamps in logs (set TZ in compose)
RUN apk add --no-cache bash inotify-tools curl python3 tzdata

COPY plex_monitor.sh /usr/local/bin/plex_monitor.sh
RUN chmod +x /usr/local/bin/plex_monitor.sh

ENTRYPOINT ["/usr/local/bin/plex_monitor.sh"]
