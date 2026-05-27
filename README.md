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
