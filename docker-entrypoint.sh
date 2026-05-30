#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
  mkdir -p /data
  if chown -R jiotv:jiotv /data 2>/dev/null; then
    exec gosu jiotv "$@"
  fi
  if gosu jiotv sh -c "test -w /data" 2>/dev/null; then
    exec gosu jiotv "$@"
  fi
  echo "warning: /data is not writable by jiotv; running as root for this container" >&2
  exec "$@"
fi

exec "$@"
