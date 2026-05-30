"""Small HTML rendering helpers for the JIO_tv web UI."""

from __future__ import annotations

import json
from html import escape
from typing import Any
from urllib.parse import quote_plus

from . import constants
from .models import Channel


def _js(value: str) -> str:
    return json.dumps(value)


def _icon(name: str, class_name: str = "app-icon") -> str:
    icons = {
        "tv": (
            '<path d="M4 6.75A2.75 2.75 0 0 1 6.75 4h10.5A2.75 2.75 0 0 1 20 6.75v7.5A2.75 2.75 0 0 1 17.25 17H6.75A2.75 2.75 0 0 1 4 14.25v-7.5Z"/>'
            '<path d="M9 20h6M12 17v3"/>'
        ),
        "search": (
            '<path d="m20 20-4.2-4.2"/>'
            '<path d="M10.8 17.1a6.3 6.3 0 1 0 0-12.6 6.3 6.3 0 0 0 0 12.6Z"/>'
        ),
        "filter": (
            '<path d="M4 7h16M7 12h10M10 17h4"/>'
        ),
        "play": (
            '<path d="M8 5.75v12.5L18 12 8 5.75Z" fill="currentColor" stroke="none"/>'
        ),
        "star": (
            '<path d="m12 3.75 2.35 4.75 5.25.77-3.8 3.7.9 5.23L12 15.72 7.3 18.2l.9-5.23-3.8-3.7 5.25-.77L12 3.75Z"/>'
        ),
        "star-fill": (
            '<path d="m12 3.75 2.35 4.75 5.25.77-3.8 3.7.9 5.23L12 15.72 7.3 18.2l.9-5.23-3.8-3.7 5.25-.77L12 3.75Z" fill="currentColor" stroke="none"/>'
        ),
        "back": (
            '<path d="M10 6 4 12l6 6"/>'
            '<path d="M4 12h11a5 5 0 0 1 0 10h-2"/>'
        ),
        "login": (
            '<path d="M15 3h3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-3"/>'
            '<path d="m10 17 5-5-5-5"/>'
            '<path d="M15 12H3"/>'
        ),
        "logout": (
            '<path d="M9 21H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3"/>'
            '<path d="m16 17 5-5-5-5"/>'
            '<path d="M21 12H9"/>'
        ),
        "sun": (
            '<path d="M12 4V2M12 22v-2M4.93 4.93 3.51 3.51M20.49 20.49l-1.42-1.42M4 12H2M22 12h-2M4.93 19.07l-1.42 1.42M20.49 3.51l-1.42 1.42"/>'
            '<path d="M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/>'
        ),
        "moon": (
            '<path d="M21 14.5A8.5 8.5 0 0 1 9.5 3a7 7 0 1 0 11.5 11.5Z"/>'
        ),
        "arrow-up": (
            '<path d="m18 15-6-6-6 6"/>'
        ),
        "wide": (
            '<path d="M4 7h16v10H4V7Z"/>'
            '<path d="M8 12h8"/>'
        ),
        "square": (
            '<path d="M7 5h10v14H7V5Z"/>'
            '<path d="M10 12h4"/>'
        ),
        "spark": (
            '<path d="m12 2 1.35 5.15L18.5 8.5l-5.15 1.35L12 15l-1.35-5.15L5.5 8.5l5.15-1.35L12 2Z"/>'
            '<path d="m18.5 14 .75 2.75L22 17.5l-2.75.75L18.5 21l-.75-2.75L15 17.5l2.75-.75L18.5 14Z"/>'
        ),
        "lock": (
            '<path d="M7 11V8a5 5 0 0 1 10 0v3"/>'
            '<path d="M6.75 11h10.5A1.75 1.75 0 0 1 19 12.75v6.5A1.75 1.75 0 0 1 17.25 21H6.75A1.75 1.75 0 0 1 5 19.25v-6.5A1.75 1.75 0 0 1 6.75 11Z"/>'
        ),
    }
    return (
        f'<svg class="{class_name}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{icons[name]}</svg>'
    )


def page(
    title: str,
    body: str,
    extra_head: str = "",
    scripts: str = "",
    body_class: str = "app-body",
) -> str:
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="theme-color" content="#07080d" />
    <link rel="manifest" href="/static/manifest.json" />
    <link href="/static/internal/tailwind.css" rel="stylesheet" />
    <link href="/static/internal/app.css" rel="stylesheet" />
    <title>{escape(title)}</title>
    {extra_head}
  </head>
  <body class="{escape(body_class)}">
    {body}
    {scripts}
  </body>
</html>"""


def navbar(title: str, is_not_logged_in: bool, player_url: str = "") -> str:
    login_button = (
        '<button onclick="login_modal.showModal()" class="app-action app-action-primary">'
        f'{_icon("login")}<span>Login</span></button>'
        if is_not_logged_in
        else '<button onclick="window.location.href=\'/logout\'" class="app-action app-action-danger">'
        f'{_icon("logout")}<span>Logout</span></button>'
    )
    if player_url:
        login_button = (
            '<button class="app-action" onclick="window.history.back()" '
            'aria-label="Go back to previous page">'
            f'{_icon("back")}<span>Back</span></button>'
        )
    return f"""
<nav class="app-nav" role="navigation" aria-label="Main navigation">
  <div class="app-nav-brand">
    <a href="/" class="brand-mark" aria-label="Go to home page">
      {_icon("tv", "brand-icon")}
      <span>{escape(title)}</span>
    </a>
  </div>
  <div class="app-nav-actions">
    <label class="nav-switch-control">
      <span id="catchup-toggle-label">Catchup: OFF</span>
      <input id="catchup-toggle" type="checkbox" class="nav-switch warn"
        onchange="toggleCatchupMode()" role="switch" aria-label="Toggle catchup mode" />
    </label>
    <label class="theme-switch swap swap-rotate" aria-label="Toggle theme">
      <input type="checkbox" onclick="toggleTheme()" aria-label="Switch theme" />
      <span id="sunIcon" class="swap-on">{_icon("sun")}</span>
      <span id="moonIcon" class="swap-off">{_icon("moon")}</span>
    </label>
    {login_button}
  </div>
</nav>"""


def login_dialog(is_not_logged_in: bool, force_open: bool = False) -> str:
    auto_open = "login_modal.showModal(); showLoginWarning();" if is_not_logged_in and force_open else ""
    return f"""
<dialog id="login_modal" class="modal modal-bottom sm:modal-middle">
  <form method="dialog" class="modal-box app-modal">
    <h3>Login</h3>
    <div class="modal-fields">
      <label for="number">Jio Mobile Number</label>
      <input id="number" type="tel" class="input input-bordered app-input"
        placeholder="9876543210" pattern="[0-9]{{10}}" minlength="10" maxlength="10" />
      <button type="button" onclick="loginOTPClick()" class="app-action app-action-primary">
        {_icon("login")}<span>Login</span>
      </button>
    </div>
  </form>
  <form method="dialog" class="modal-backdrop"><button>close</button></form>
</dialog>
<dialog id="verify_otp_modal" class="modal modal-bottom sm:modal-middle">
  <form method="dialog" class="modal-box app-modal">
    <h3>Verify OTP</h3>
    <div class="modal-fields">
      <label for="otp">OTP</label>
      <input id="otp" type="number" class="input input-bordered app-input" placeholder="543210" />
      <button type="button" onclick="loginOTPVerifyClick()" class="app-action app-action-primary">
        {_icon("spark")}<span>Verify</span>
      </button>
    </div>
  </form>
  <form method="dialog" class="modal-backdrop"><button>close</button></form>
</dialog>
<script>{auto_open}</script>"""


def render_index(
    title: str,
    channels: list[Channel],
    is_not_logged_in: bool,
    login_required: bool = False,
    error: str = "",
) -> str:
    cards = "\n".join(
        render_channel_card(channel, is_not_logged_in=is_not_logged_in)
        for channel in channels
    )
    if not cards:
        message = escape(error or "Try another category, language, or search term.")
        cards = """
    <div class="empty-state">
      <div class="empty-state-icon">""" + _icon("tv") + """</div>
      <h2>No channels found</h2>
      <p>""" + message + """</p>
    </div>"""
    category_options = "".join(
        f'<option value="{key}">{escape(value)}</option>'
        for key, value in constants.CATEGORY_MAP.items()
    )
    language_options = "".join(
        f'<option value="{key}">{escape(value)}</option>'
        for key, value in constants.LANGUAGE_MAP.items()
    )
    channel_count = len(channels)
    hd_count = sum(1 for channel in channels if channel.is_hd)
    catchup_count = sum(1 for channel in channels if channel.is_catchup_available)
    body = f"""
{navbar(title, is_not_logged_in)}
<main class="app-main">
  <section class="library-header" aria-labelledby="library-title">
    <div>
      <p class="section-kicker">JIO_tv</p>
      <h1 id="library-title">Live TV</h1>
      <div class="stat-strip" aria-label="Channel stats">
        <span>{channel_count} {'channel' if channel_count == 1 else 'channels'}</span>
        <span>{hd_count} HD</span>
        <span>{catchup_count} catchup</span>
      </div>
    </div>
    <div id="portexe-search-root" class="control-surface">
      <label class="search-control" for="portexe-search-input">
        {_icon("search")}
        <input id="portexe-search-input" type="search" placeholder="Search channels"
          autocomplete="off" />
      </label>
      <select id="portexe-quality-select" class="app-select" onchange="onQualityChange(this)"
        aria-label="Quality">
        <option value="auto">Quality: Auto</option>
        <option value="high">Quality: High</option>
        <option value="medium">Quality: Medium</option>
        <option value="low">Quality: Low</option>
      </select>
      <select id="portexe-category-select" class="app-select" aria-label="Category">
        {category_options}
      </select>
      <select id="portexe-language-select" class="app-select" aria-label="Language">
        {language_options}
      </select>
      <button id="portexe-search-button" class="app-action app-action-primary">
        {_icon("filter")}<span>Apply</span>
      </button>
    </div>
  </section>

  <section id="favorite-channels-section" class="content-rail" style="display: none;">
    <div class="rail-heading">
      <h2>Favorites</h2>
    </div>
    <div id="favorite-channels-container" class="channel-grid"></div>
  </section>

  <section class="content-rail" aria-labelledby="all-channels-title">
    <div class="rail-heading">
      <h2 id="all-channels-title">All Channels</h2>
    </div>
    <div id="original-channels-grid" class="channel-grid">
      {cards}
    </div>
  </section>

  <button class="floating-action" onclick="scrollToTop()" aria-label="Back to top">
    {_icon("arrow-up")}
  </button>
  <div id="login-warning-toast" class="login-warning-toast" role="status" aria-live="polite">
    {_icon("lock")}<span>Login required to play JIO channels.</span>
  </div>
</main>
{login_dialog(is_not_logged_in, login_required)}"""
    scripts = """
<script src="/static/internal/utils.js"></script>
<script src="/static/internal/index.js"></script>
<script src="/static/internal/channels.js"></script>
<script src="/static/internal/common.js"></script>"""
    return page(title, body, scripts=scripts)


def render_channel_card(channel: Channel, is_not_logged_in: bool = False) -> str:
    requires_login = is_not_logged_in and not channel.id.startswith("cc_")
    category = constants.CATEGORY_MAP.get(channel.category, "Live")
    language = constants.LANGUAGE_MAP.get(channel.language, "")
    badges = ""
    if requires_login:
        badges += f'<span class="tile-badge tile-badge-lock">{_icon("lock")}Login</span>'
    if channel.is_hd:
        badges += '<span class="tile-badge">HD</span>'
    if channel.is_catchup_available:
        badges += '<span class="tile-badge tile-badge-warm">Replay</span>'
    if not badges and category:
        badges = f'<span class="tile-badge">{escape(category)}</span>'
    meta = " / ".join(item for item in (category, language) if item)
    return f"""
<a href="{"/?login=required" if requires_login else "/play/" + escape(channel.id)}"
  class="card channel-card group{' is-locked' if requires_login else ''}"
  data-channel-id="{escape(channel.id)}"
  data-channel-name="{escape(channel.name)}"
  {'data-requires-login="true" onclick="warnLoginRequired(event)"' if requires_login else ''}
  tabindex="0">
  <div class="channel-art">
    <img src="{escape(channel.logo_url)}" loading="lazy" alt="{escape(channel.name)}"
      onerror="this.style.visibility='hidden'" />
    <div class="channel-glow" aria-hidden="true"></div>
  </div>
  <div class="channel-copy">
    <span class="font-bold channel-title">{escape(channel.name)}</span>
    <span class="channel-meta">{escape(meta or "Live")}</span>
  </div>
  <div class="tile-badges">{badges}</div>
  <button id="favorite-btn-{escape(channel.id)}" class="favorite-btn" type="button"
    data-favorite-id="{escape(channel.id)}" aria-label="Toggle favorite"
    onclick="event.preventDefault(); event.stopPropagation(); toggleFavorite(this.dataset.favoriteId);">
    <span id="star-icon-{escape(channel.id)}">{_icon("star")}</span>
    <span id="x-icon-{escape(channel.id)}" class="hidden">{_icon("star-fill")}</span>
  </button>
</a>"""


def render_play(
    title: str,
    player_url: str,
    channel_id: str,
    *,
    quality: str = "auto",
    mode: str = "hls",
    drm_enabled: bool = False,
) -> str:
    mode_link = ""
    if drm_enabled:
        next_mode = "hls" if mode == "hd" else "hd"
        next_label = "Use HLS" if mode == "hd" else "Use HD"
        mode_link = (
            f'<a class="app-action" '
            f'href="/play/{escape(channel_id)}?q={escape(quality)}&pm={next_mode}">'
            f'{_icon("spark")}<span>{next_label}</span></a>'
        )
    body = f"""
{navbar(title, False, player_url)}
<main class="watch-page">
  <section class="watch-layout">
    <div class="watch-stage">
      <div class="player-toolbar">
        <div class="live-chip"><span></span>Live</div>
        <div class="player-actions">
          {mode_link}
          <button id="aspect-43-toggle" class="icon-action" onclick="toggleAspectRatio()"
            aria-label="Toggle aspect ratio">
            <span id="aspect-wide-icon">{_icon("wide")}</span>
            <span id="aspect-square-icon" hidden>{_icon("square")}</span>
          </button>
        </div>
      </div>
      <div id="player" class="player-shell" style="aspect-ratio: 16/9; min-height: 220px">
        <iframe id="playerIframe" src="{escape(player_url)}" data-base-src="{escape(player_url)}"
          loading="eager" fetchpriority="high" allowfullscreen
          allow="autoplay; encrypted-media; fullscreen"></iframe>
      </div>
    </div>

    <aside id="epg_parent" class="epg-panel" style="display: none;">
      <div class="rail-heading">
        <h2>Now Playing</h2>
      </div>
      <div id="epg" class="epg-card">
        <figure class="epg-poster-frame">
          <img id="episodePoster" alt="Episode Poster" />
        </figure>
        <div class="epg-copy">
          <h3 id="showname"></h3>
          <div class="epg-live-row">
            <span class="live-chip compact"><span></span>Live</span>
            <div class="countdown-copy">
              <span>Ends in</span>
              <span id="countdown_hour" class="countdown"><span id="e_hour" style="--value:0;"></span>h</span>
              <span id="countdown_minute" class="countdown"><span id="e_minute" style="--value:0;"></span>m</span>
              <span id="countdown_second" class="countdown"><span id="e_second" style="--value:0;"></span>s</span>
            </div>
          </div>
          <p id="description"></p>
          <div id="keywords" class="keyword-row"></div>
        </div>
      </div>
    </aside>
  </section>

  <section id="similar_channels_parent" class="content-rail watch-rail" style="display: none;">
    <div class="rail-heading">
      <h2>Similar Channels</h2>
    </div>
    <div id="similar_channels" class="channel-grid compact-grid"></div>
  </section>
</main>
<script>
const channelId = {channel_id!r};
let current43Mode = false;

function updateAspectRatioUI(is43Mode) {{
  const playerContainer = document.getElementById("player");
  const button = document.getElementById("aspect-43-toggle");
  const wideIcon = document.getElementById("aspect-wide-icon");
  const squareIcon = document.getElementById("aspect-square-icon");
  if (playerContainer) playerContainer.style.aspectRatio = is43Mode ? "4 / 3" : "16 / 9";
  if (button) button.classList.toggle("is-active", is43Mode);
  if (wideIcon) wideIcon.hidden = is43Mode;
  if (squareIcon) squareIcon.hidden = !is43Mode;
}}

function toggleAspectRatio() {{
  current43Mode = !current43Mode;
  if (current43Mode) {{
    localStorage.setItem("aspectRatio_" + channelId, "4/3");
  }} else {{
    localStorage.removeItem("aspectRatio_" + channelId);
  }}
  updateAspectRatioUI(current43Mode);
}}

document.addEventListener("DOMContentLoaded", function () {{
  current43Mode = localStorage.getItem("aspectRatio_" + channelId) === "4/3";
  updateAspectRatioUI(current43Mode);
}});
</script>"""
    scripts = """
<script src="/static/internal/utils.js"></script>
<script src="/static/internal/common.js"></script>
<script src="/static/internal/epg.js"></script>"""
    return page(title, body, scripts=scripts)


def render_hls_player(
    play_url: str,
    is_catchup: bool = False,
    channel_id: str = "",
) -> str:
    live = "false" if is_catchup else "true"
    seekable = "true" if is_catchup else "false"
    retry = "false" if is_catchup else "true"
    smooth_start_seconds = (
        constants.SMOOTH_START_OFFSET_SECONDS
        if not is_catchup and channel_id in constants.SMOOTH_START_CHANNELS
        else 0
    )
    preload = (
        f'<link rel="preload" href="{escape(play_url)}" as="fetch" crossorigin="anonymous" />'
        if not is_catchup and play_url.startswith("/live/")
        else ""
    )
    body = f"""
<div id="JIO_tv_player"></div>
<script>
  flowplayer("#JIO_tv_player", {{
    src: {_js(play_url)},
    auto_orient: true,
    autoplay: true,
    live: {live},
    seekable: {seekable},
    retry: {retry},
    qsel: {{}},
    asel: {{}},
    hlsjs: {{
      capLevelToPlayerSize: false,
      lowLatencyMode: true,
      maxBufferLength: 15,
      maxMaxBufferLength: 20,
      backBufferLength: 10,
      liveBackBufferLength: 10,
      liveSyncDurationCount: 2,
      liveMaxLatencyDurationCount: 4,
      maxBufferHole: 0.5,
      maxFragLookUpTolerance: 0.5,
      manifestLoadingMaxRetry: 4,
      levelLoadingMaxRetry: 4,
      fragLoadingMaxRetry: 6,
      fragLoadingRetryDelay: 700,
      fragLoadingMaxRetryTimeout: 8000,
      startFragPrefetch: true,
      startLevel: -1
    }},
    ui: flowplayer.ui.USE_THIN_CONTROLBAR,
  }});

  const smoothStartSeconds = {smooth_start_seconds};
  if (smoothStartSeconds > 0) {{
    const attachSmoothStart = () => {{
      const video = document.querySelector("#JIO_tv_player video");
      if (!video) return false;
      let applied = false;
      const seek = () => {{
        if (applied) return;
        try {{
          let target = smoothStartSeconds;
          if (video.seekable && video.seekable.length > 0) {{
            const start = video.seekable.start(0);
            const end = video.seekable.end(video.seekable.length - 1);
            target = Math.max(start, end - smoothStartSeconds);
          }}
          if (Number.isFinite(target) && video.currentTime < target) {{
            video.currentTime = target;
          }}
          applied = true;
        }} catch (error) {{}}
      }};
      video.addEventListener("loadedmetadata", seek, {{ once: true }});
      video.addEventListener("canplay", seek, {{ once: true }});
      video.addEventListener("playing", seek, {{ once: true }});
      video.addEventListener("ended", () => {{
        applied = false;
        seek();
        video.play().catch(() => {{}});
      }});
      seek();
      return true;
    }};
    if (!attachSmoothStart()) {{
      const observer = new MutationObserver(() => {{
        if (attachSmoothStart()) observer.disconnect();
      }});
      observer.observe(document.getElementById("JIO_tv_player"), {{
        childList: true,
        subtree: true
      }});
    }}
  }}
</script>"""
    head = f"""
<style>body{{margin:0;padding:0;background-color:#000;overflow-y:hidden}}</style>
{preload}
<link rel="stylesheet" href="/static/external/flowplayer.css" />
<script src="/static/external/flowplayer.min.js"></script>
<script src="/static/external/hls.min.js"></script>
<script src="/static/external/qsel.min.js"></script>
<script src="/static/external/asel.min.js"></script>"""
    return page("JIO_tv", body, extra_head=head, body_class="player-frame")


def render_drm_player(
    play_url: str,
    license_url: str = "",
    channel_host: str = "",
    channel_path: str = "",
    hls_fallback_url: str = "",
    hls_player_fallback_url: str = "",
    player_mode: str = "auto",
) -> str:
    body = f"""
<div data-shaka-player-container>
  <video autoplay data-shaka-player id="JIO_tv_player" style="width:100%;height:100%"></video>
</div>
<script>
document.addEventListener("shaka-ui-loaded", async () => {{
  const video = document.getElementById("JIO_tv_player");
  const ui = video["ui"];
  const controls = ui.getControls();
  const player = controls.getPlayer();
  const licenseUrl = {_js(license_url)};
  player.configure({{
    streaming: {{
      bufferingGoal: 15,
      rebufferingGoal: 2,
      lowLatencyMode: true,
      stallEnabled: true,
      stallThreshold: 1,
      stallSkip: 0.1,
      retryParameters: {{maxAttempts: 4, baseDelay: 750, backoffFactor: 1.6, timeout: 15000}}
    }},
    manifest: {{
      retryParameters: {{maxAttempts: 4, baseDelay: 750, backoffFactor: 1.6, timeout: 15000}}
    }},
    abr: {{
      defaultBandwidthEstimate: 700000,
      switchInterval: 2
    }}
  }});
  if (licenseUrl) {{
    player.configure({{drm: {{servers: {{"com.widevine.alpha": licenseUrl}}}}}});
  }}
  try {{
    await player.load({_js(play_url)});
  }} catch (error) {{
    const fallback = {_js(hls_player_fallback_url or hls_fallback_url)};
    if (fallback && {_js(player_mode)} === "auto") window.location.replace(fallback);
    else console.error(error);
  }}
}});
</script>"""
    head = """
<style>body{margin:0;padding:0;background-color:#000;overflow-y:hidden}</style>
<script src="/static/external/shaka-player.ui.js"></script>
<link rel="stylesheet" href="/static/external/shaka-player-controls.css" />"""
    return page("JIO_tv", body, extra_head=head, body_class="player-frame")


def render_catchup(
    title: str,
    channel: str,
    data: list[dict[str, Any]] | None = None,
    error: str = "",
    **context: Any,
) -> str:
    items = ""
    for item in data or []:
        show_raw = str(item.get("showname", "Catchup Show"))
        show = escape(show_raw)
        start = escape(str(item.get("showtime", "")))
        srno = escape(str(item.get("srno", "")))
        start_epoch = escape(str(item.get("startEpoch", "")))
        end_epoch = escape(str(item.get("endEpoch", "")))
        items += (
            f'<a class="catchup-card" '
            f'href="/catchup/play/{escape(channel)}?start={start_epoch}&end={end_epoch}'
            f'&srno={srno}&showname={quote_plus(show_raw)}">'
            f'<span class="catchup-time">{start}</span>'
            f'<strong>{show}</strong>'
            f'<span class="catchup-play">{_icon("play")}<span>Play</span></span></a>'
        )
    if not items:
        items = """
    <div class="empty-state">
      <div class="empty-state-icon">""" + _icon("tv") + """</div>
      <h2>No catchup programs</h2>
      <p>Live playback is still available for this channel.</p>
    </div>"""
    body = f"""
{navbar(title, False)}
<main class="app-main">
  <section class="library-header compact" aria-labelledby="catchup-title">
    <div>
      <p class="section-kicker">Replay</p>
      <h1 id="catchup-title">Catchup</h1>
    </div>
  </section>
  {'<p class="app-error">' + escape(error) + '</p>' if error else ''}
  <section class="content-rail">
    <div class="catchup-grid">{items}</div>
  </section>
</main>"""
    scripts = """
<script src="/static/internal/utils.js"></script>
<script src="/static/internal/common.js"></script>"""
    return page(title, body, scripts=scripts)


def render_catchup_player(
    title: str,
    player_url: str,
    show_name: str,
    description: str,
    poster: str,
) -> str:
    body = f"""
{navbar(title, False, player_url)}
<main class="watch-page">
  <section class="watch-layout">
    <div class="watch-stage">
      <div class="player-shell" style="aspect-ratio:16/9">
        <iframe src="{escape(player_url)}" allowfullscreen allow="autoplay"></iframe>
      </div>
    </div>
    <aside class="details-panel">
      {f'<img class="details-poster" src="{escape(poster)}" alt="{escape(show_name)}" />' if poster else ''}
      <p class="section-kicker">Catchup</p>
      <h1>{escape(show_name)}</h1>
      <p>{escape(description)}</p>
    </aside>
  </section>
</main>"""
    scripts = """
<script src="/static/internal/utils.js"></script>
<script src="/static/internal/common.js"></script>"""
    return page(title, body, scripts=scripts)
