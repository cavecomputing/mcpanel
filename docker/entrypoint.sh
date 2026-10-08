#!/bin/sh
set -e

: "${MCPANEL_DATA_DIR:?Set MCPANEL_DATA_DIR to the absolute path of the data folder on the host}"
mkdir -p "$MCPANEL_DATA_DIR/logs" "$MCPANEL_DATA_DIR/servers"

# If running as root, give the data folder to PUID:PGID and re-exec as them (setpriv is util-linux's
# gosu, already in the image). Only what isn't theirs yet changes, and the worlds under servers/ are
# skipped: their containers run as the same user, and walking them would slow every start.
if [ "$(id -u)" = "0" ]; then
    TARGET_UID="${PUID:-1000}"
    TARGET_GID="${PGID:-1000}"
    find "$MCPANEL_DATA_DIR" -path "$MCPANEL_DATA_DIR/servers/*" -prune -o \
        \( ! -user "$TARGET_UID" -o ! -group "$TARGET_GID" \) -exec chown "$TARGET_UID:$TARGET_GID" {} +
    # The Docker socket's group, so the panel can reach Docker without being root. Without the
    # socket it starts anyway and says Docker is unreachable.
    GROUPS_FLAG=--clear-groups
    if [ -S /var/run/docker.sock ]; then
        GROUPS_FLAG="--groups=$(stat -c %g /var/run/docker.sock)"
    fi
    exec setpriv --reuid="$TARGET_UID" --regid="$TARGET_GID" "$GROUPS_FLAG" -- "$@"
fi

exec "$@"
