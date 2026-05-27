"""EPG generation with Python thread-pool workers."""

from __future__ import annotations

import gzip
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from . import constants
from .http_client import request
from .scheduler import scheduler
from .utils import get_path_prefix, log, read_json_response


def init() -> None:
    epg_file = get_path_prefix() / "epg.xml.gz"
    should_generate = not epg_file.exists()
    if epg_file.exists():
        modified = datetime.fromtimestamp(epg_file.stat().st_mtime)
        should_generate = modified.date() != datetime.now().date()

    if should_generate:
        try:
            gen_xml_gz(epg_file)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to generate EPG file: %s", exc)

    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    hour = -5 + random.randint(0, 2)
    minute = -30 + random.randint(0, 59)
    schedule_time = datetime.combine(tomorrow, datetime.min.time(), timezone.utc)
    schedule_time += timedelta(hours=hour, minutes=minute)
    delay = max((schedule_time - datetime.now(timezone.utc)).total_seconds(), 1)
    scheduler.add(constants.EPG_TASK_ID, delay, lambda: gen_xml_gz(epg_file))


def gen_xml_gz(filename: str | Path) -> None:
    xml = gen_xml()
    header = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<!DOCTYPE tv SYSTEM "http://www.w3.org/2006/05/tv">'
    )
    with gzip.open(filename, "wb") as file:
        file.write(header + xml)


def gen_xml() -> bytes:
    data = read_json_response(constants.CHANNEL_URL)
    channel_items = data.get("result", [])
    channels = [
        {
            "id": int(item.get("channel_id", 0)),
            "display": str(item.get("channel_name", "")),
        }
        for item in channel_items
    ]
    programmes: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_channel_epg, channel["id"]) for channel in channels]
        for future in as_completed(futures):
            programmes.extend(future.result())

    root = Element("tv")
    for channel in channels:
        channel_el = SubElement(root, "channel", {"id": str(channel["id"])})
        display_el = SubElement(channel_el, "display-name")
        display_el.text = channel["display"]

    for programme in programmes:
        programme_el = SubElement(
            root,
            "programme",
            {
                "channel": str(programme["channel_id"]),
                "start": _format_time(programme["start_epoch"]),
                "stop": _format_time(programme["end_epoch"]),
            },
        )
        title = SubElement(programme_el, "title", {"lang": "en"})
        title.text = programme.get("showname", "")
        desc = SubElement(programme_el, "desc", {"lang": "en"})
        desc.text = programme.get("description", "")
        category = SubElement(programme_el, "category", {"lang": "en"})
        category.text = programme.get("showCategory", "")
        SubElement(
            programme_el,
            "icon",
            {"src": f"{constants.EPG_POSTER_URL}/{programme.get('episodePoster', '')}"},
        )
    return tostring(root, encoding="utf-8")


def fetch_channel_epg(channel_id: int) -> list[dict[str, Any]]:
    programmes: list[dict[str, Any]] = []
    for offset in range(2):
        url = constants.EPG_URL % (offset, channel_id)
        resp = request(url, headers={constants.USER_AGENT: constants.USER_AGENT_OKHTTP})
        if resp.status != 200:
            log.warning("EPG fetch failed for channel %s offset %s", channel_id, offset)
            continue
        try:
            data = resp.json()
        except ValueError:
            continue
        for programme in data.get("epg", []):
            programmes.append(
                {
                    "channel_id": channel_id,
                    "start_epoch": int(programme.get("startEpoch", 0) or 0),
                    "end_epoch": int(programme.get("endEpoch", 0) or 0),
                    "showname": programme.get("showname", ""),
                    "description": programme.get("description", ""),
                    "showCategory": programme.get("showCategory", ""),
                    "episodePoster": programme.get("episodePoster", ""),
                }
            )
    return programmes


def _format_time(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, timezone.utc).strftime(
        "%Y%m%d%H%M%S %z"
    )
