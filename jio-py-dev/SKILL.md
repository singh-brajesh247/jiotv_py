---
name: jio-py-dev
description: Develop, debug, and extend the jio_py JIO_tv Python streaming app. Use when Codex works on jio_py UI, playback, login/token storage, DRM/DASH/HLS routing, Sony channel fixes, server startup, tests, or app identity changes.
---

# jio_py Development

## Quick Workflow

1. Start in the repo root, usually `/Users/kbrajesh/Downloads/tv`.
2. Prefer `rg`/`rg --files` for discovery.
3. Keep `jio_py` independent from sibling projects. Do not add new source references to `jio_go`, `jiotv_go`, or `jiotv_py`.
4. Make narrowly scoped edits that match existing stdlib Python patterns.
5. Run the verification commands before finishing.

## Project Map

- `jio_py/server.py`: HTTP routes, login guards, HLS rendering, MPD/DASH/DRM proxying, server startup.
- `jio_py/templates.py`: server-rendered HTML, channel grid, player pages, Shaka/Flowplayer setup.
- `jio_py/web/static/internal/app.css`: modern TV UI styles.
- `jio_py/web/static/internal/index.js`: dashboard search, favorites, login warning behavior.
- `jio_py/streaming.py`: URL selection, HDNEA extraction/cache, manifest rewrite helpers.
- `jio_py/television.py`: JioTV API client, custom channels, Sony helper URLs.
- `jio_py/store.py`: token/config data store, default path handling.
- `jio_py/config.py`: config discovery and env mapping.
- `jio_py/constants.py`: app identity, static roots, channel constants.
- `jio_py/tests/test_conversion.py`: current regression suite.

## Core Rules

- App identity is `JIO_tv`; CLI module remains `jio_py`.
- Default token store is `~/.JIO_tv/store_v4.toml`, unless `JIOTV_PATH_PREFIX` or config `path_prefix` overrides it.
- Login-protected real channels must warn/redirect when logged out. Custom channels can remain playable without login.
- `jio_py/constants.py` should keep `WEB_ROOT` inside the package so the app runs independently.
- DRM defaults to enabled. Sony and HD playback depend on it.
- After changing JS strings inside `<script>`, use JSON string literals rather than HTML escaping, so query `&channel_id=` does not become `&amp;channel_id=`.

## Playback Notes

- Normal HLS page: `/play/<id>` -> `/player/<id>` -> `/live/...` -> `/render.m3u8`.
- DRM page: `/play/<id>` -> `/mpd/<id>` -> `/render.mpd` -> `/render.dash/...` and `/drm`.
- Sony channels in `constants.SONY_LIST` should route to `/mpd/<id>` when DRM is enabled. Avoid forcing dead Sony HLS URLs such as `Sony_Max_SD_250.m3u8`.
- `/render.mpd` must rewrite MPD `<BaseURL>` values to local `/render.dash/...` paths so browser segment requests stay proxied.
- `/render.dash` must preserve HDNEA where present and retry once after credential refresh on 401/403.
- `/drm` must preserve Shaka's POST body and proxy it as POST with DRM headers and cookies from the MPD HEAD request.
- If logs show query keys like `amp;channel_id`, inspect `templates.py` for HTML-escaped JavaScript URLs.

## UI Notes

- Build the usable TV app surface, not a marketing page.
- Keep the interface dense, futuristic, and remote-control friendly.
- Use stable dimensions for channel tiles, toolbars, and player frames to avoid layout shifts.
- Channel cards should show locked state and login warning when logged out.
- Keep text compact and avoid visible how-to explanations inside the app.

## Common Commands

Run these from `/Users/kbrajesh/Downloads/tv`:

```bash
python3 -m compileall -q jio_py
python3 -m unittest discover jio_py/tests
node --check jio_py/web/static/internal/index.js
node --check jio_py/web/static/internal/epg.js
rg -n "jio_?go|jiotv_go|JioTV Go|jiotv_py|\\.jiotv_go|jiotv_go\\.log" jio_py
```

Start the server for manual testing:

```bash
python3 -m jio_py serve -H localhost -p 5001 --log-stdout
python3 -m jio_py serve -H 0.0.0.0 -p 5001 --log-stdout
```

Binding to `0.0.0.0` may need approval in restricted environments. Stop long-running server sessions before finishing unless the user asked to keep them running.

## Log Triage

- `config loaded ... drm=False`: config disabled DRM or app started with stale defaults.
- `render m3u8 ... Sony... -> 404`: Sony channel is still hitting stale HLS; check routing to `/mpd`.
- `/drm ... channel=` empty: query escaping or template JS string bug.
- `/drm -> 404` with valid channel: check POST body forwarding and cookies.
- Segment requests direct to CDN from the browser: MPD BaseURL rewrite failed.
- Repeated `/render.mpd` requests but successful `/render.dash` segments can be Shaka live refresh behavior; focus on `/drm` and segment status codes.

## Finish Criteria

- Mention changed files and the behavior fixed.
- State verification commands and results.
- If playback could not be tested live, say that explicitly and give the exact log line/request to inspect next.
