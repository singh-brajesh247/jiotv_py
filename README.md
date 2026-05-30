# JIO_tv Python App

`jio_py` runs as a standalone Python package and serves its own bundled web
assets from `jio_py/web`.

Start the server:

```bash
python3 -m jio_py serve --host localhost --port 5001
```

For stream troubleshooting, mirror logs to the terminal and enable verbose
upstream request diagnostics:

```bash
python3 -m jio_py serve --host localhost --port 5001 --log-stdout --debug-log
```

Logs are written to `JIO_tv.log` under the configured `path_prefix`. If no
`path_prefix` is configured, the default is `$HOME/.JIO_tv/JIO_tv.log`.

Useful commands:

```bash
python3 -m jio_py login otp
python3 -m jio_py login reset
python3 -m jio_py epg generate
python3 -m jio_py epg delete
python3 -m jio_py background start --args "--host localhost --port 5001"
python3 -m jio_py background stop
```

Scale-oriented defaults are built into the Python server:

- `asgi_worker_threads: 256` keeps blocking upstream work in a bounded pool
  instead of creating one OS thread per request.
- `asgi_request_queue_limit: 5000` accepts large bursts and returns HTTP 503
  when the process is saturated instead of exhausting memory.
- `channels_cache_ttl: 300`, `manifest_cache_ttl: 3`, and
  `segment_cache_ttl: 20` share repeated channel, playlist, manifest, and media
  segment work across viewers.
- `segment_cache_max_bytes: 536870912` and
  `segment_cache_item_max_bytes: 8388608` cap the in-process media cache.

For thousands of concurrent clients, run behind a reverse proxy and expose the
server with `--host 0.0.0.0`. For very large public traffic, put a CDN or caching
proxy in front of the stream endpoints; one Python process cannot serve millions
of concurrent video clients by itself.

When a stream fails, check the log lines around:

- `live route selected`: channel and selected upstream HLS URL.
- `render m3u8 start`: decrypted manifest URL and cached token state.
- `render upstream status`: upstream status, response size, and redacted URL.
- `manifest rewrite`: number of media/key references rewritten.
- `render segment`: `.ts`/`.aac` segment proxy requests.
- `mpd selected` and `drm key`: DRM/DASH playback path.

The `/play/<channel>` page starts with the faster HLS player by default. If
`drm: true` is configured, use the `Use HD` button on the play page or add
`?pm=hd` to the play URL to force DRM/DASH.
